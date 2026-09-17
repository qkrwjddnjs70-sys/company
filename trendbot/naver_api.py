"""네이버 데이터랩 검색어트렌드 오픈API 클라이언트 (stdlib만 사용).

공식 문서: https://developers.naver.com/docs/serviceapi/datalab/search/search.md
안정된 공식 API라 hsbot/sources/datahub.py와 달리 엔드포인트를 설정 파일로
빼지 않고 코드에 그대로 둔다. 인증 키만 환경변수로 받는다.

중요 — 키워드 그룹핑 규칙
    한 그룹에 키워드를 여러 개 넣으면 네이버는 그 키워드들을 합산한 비율을
    하나로 돌려준다(개별 비교 불가). 그래서 키워드별로 따로 보려면 반드시
    "그룹 1개 = 키워드 1개"로 나눠 보내야 한다. 이 클라이언트는 항상 그렇게 보낸다.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta

API_URL = "https://openapi.naver.com/v1/datalab/search"
CLIENT_ID_ENV = "TRENDBOT_NAVER_CLIENT_ID"
CLIENT_SECRET_ENV = "TRENDBOT_NAVER_CLIENT_SECRET"

# 데이터랩 API 제약: 호출 1회에 그룹 최대 5개, 그룹당 키워드 최대 20개.
# 그룹=키워드 1개로 고정하므로 실질적으로 "호출 1회당 키워드 5개"가 된다.
MAX_GROUPS_PER_CALL = 5


class NaverApiError(RuntimeError):
    pass


@dataclass
class TrendPoint:
    period: str  # "YYYY-MM-DD"
    ratio: float  # 구간 내 최대치를 100으로 둔 상대 검색량 (절대 검색량이 아님)


@dataclass
class TrendSeries:
    keyword: str
    points: list[TrendPoint]

    def ratios(self) -> list[float]:
        return [p.ratio for p in self.points]


def _chunked(seq: list, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def date_n_years_ago(years: int, *, from_date: date | None = None) -> date:
    d = from_date or date.today()
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 2/29 윤년 보정
        return d.replace(month=2, day=28, year=d.year - years)


def date_n_weeks_ago(weeks: int, *, from_date: date | None = None) -> date:
    d = from_date or date.today()
    return d - timedelta(weeks=weeks)


class NaverDataLabClient:
    """검색어트렌드 API 호출 + 일 단위 캐시.

    데이터랩 API는 하루 호출 한도가 있고 응답도 하루 단위로만 갱신되므로,
    같은 날 같은 요청은 캐시로 재사용한다(급상승 탐지처럼 키워드가 많을 때
    한도를 아끼는 데 특히 중요하다).
    """

    def __init__(
        self,
        *,
        client_id: str | None = None,
        client_secret: str | None = None,
        cache_dir: str | None = ".cache/trendbot",
        timeout_sec: float = 10.0,
        rate_limit_sec: float = 0.2,
        max_retries: int = 3,
    ):
        self.client_id = client_id or os.environ.get(CLIENT_ID_ENV, "")
        self.client_secret = client_secret or os.environ.get(CLIENT_SECRET_ENV, "")
        self.cache_dir = cache_dir
        self.timeout_sec = timeout_sec
        self.rate_limit_sec = rate_limit_sec
        self.max_retries = max_retries
        self._last_call = 0.0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def _require_credentials(self) -> None:
        if not self.client_id or not self.client_secret:
            raise NaverApiError(
                "네이버 API 인증 정보가 없습니다. 환경변수를 설정하세요.\n"
                f"  export {CLIENT_ID_ENV}=발급받은_클라이언트_ID\n"
                f"  export {CLIENT_SECRET_ENV}=발급받은_클라이언트_시크릿\n"
                "  (developers.naver.com → 애플리케이션 등록 → '검색' API 사용 신청,\n"
                "   데이터랩 검색어트렌드는 별도 신청 없이 '검색' API 키로 바로 쓸 수 있습니다.)"
            )

    def _cache_path(self, body: bytes) -> str | None:
        if not self.cache_dir:
            return None
        h = hashlib.sha256(body).hexdigest()
        return os.path.join(self.cache_dir, f"{date.today().isoformat()}_{h}.json")

    def _post(self, payload: dict) -> dict:
        self._require_credentials()
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        cache_path = self._cache_path(body)
        if cache_path and os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)

        headers = {
            "Content-Type": "application/json",
            "X-Naver-Client-Id": self.client_id,
            "X-Naver-Client-Secret": self.client_secret,
        }
        last_err: Exception | None = None
        delay = 1.0
        for attempt in range(self.max_retries):
            gap = time.monotonic() - self._last_call
            if gap < self.rate_limit_sec:
                time.sleep(self.rate_limit_sec - gap)
            req = urllib.request.Request(API_URL, data=body, method="POST", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self._last_call = time.monotonic()
                if cache_path:
                    with open(cache_path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                return data
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")
                if e.code in (401, 403):
                    raise NaverApiError(
                        f"HTTP {e.code} — 인증 실패입니다. Client ID/Secret과 "
                        f"애플리케이션의 '검색' API 사용 신청 상태를 확인하세요.\n{detail}"
                    ) from e
                if e.code == 429:
                    last_err = e  # 한도 초과는 잠시 후 재시도해볼 가치가 있다
                else:
                    raise NaverApiError(f"HTTP {e.code} {e.reason}: {detail}") from e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
            if attempt < self.max_retries - 1:
                time.sleep(delay)
                delay *= 2
        raise NaverApiError(f"요청 실패({self.max_retries}회 재시도): {last_err}")

    def search_trend(
        self,
        keywords: list[str],
        *,
        start_date: str,
        end_date: str,
        time_unit: str = "month",
    ) -> dict[str, TrendSeries]:
        """키워드별 개별 추이를 가져온다. (그룹=키워드 1개로 고정)"""
        if not keywords:
            return {}
        out: dict[str, TrendSeries] = {}
        for chunk in _chunked(keywords, MAX_GROUPS_PER_CALL):
            payload = {
                "startDate": start_date,
                "endDate": end_date,
                "timeUnit": time_unit,
                "keywordGroups": [{"groupName": kw, "keywords": [kw]} for kw in chunk],
            }
            data = self._post(payload)
            for result in data.get("results", []):
                title = result.get("title", "")
                points = [
                    TrendPoint(period=str(d.get("period", "")), ratio=float(d.get("ratio", 0.0)))
                    for d in result.get("data", [])
                ]
                out[title] = TrendSeries(keyword=title, points=points)
        return out
