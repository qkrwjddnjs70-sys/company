"""네이버 검색광고(SearchAd) 키워드도구 API — 연관 키워드 + 월간 검색량.

지금까지 쓰던 데이터랩 검색어트렌드 API와는 완전히 다른 시스템이다.
- 계정: developers.naver.com/NCP가 아니라 https://searchad.naver.com 검색광고 계정
- 인증: X-Naver-Client-Id/Secret도, X-NCP-APIGW-*도 아닌 HMAC-SHA256 서명
  (타임스탬프 + 메서드 + URI를 SECRET_KEY로 서명해 X-Signature로 보낸다)

"써큘레이터"처럼 넓은 키워드를 넣으면 "신일써큘레이터", "한일써큘레이터"처럼
브랜드가 붙은 연관 키워드와 각각의 월간 검색량(PC/모바일)을 함께 준다 —
데이터랩에는 없는, 이 API만의 장점이다. 다만 데이터랩의 ratio(상대 지표)와 달리
여기 검색량은 절대 수치이되, 10 미만은 "< 10"이라는 문자열로만 온다.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

API_HOST = "https://api.searchad.naver.com"
KEYWORDS_TOOL_URI = "/keywordstool"

API_KEY_ENV = "TRENDBOT_SEARCHAD_API_KEY"       # 액세스라이선스
SECRET_KEY_ENV = "TRENDBOT_SEARCHAD_SECRET_KEY"  # 비밀키
CUSTOMER_ID_ENV = "TRENDBOT_SEARCHAD_CUSTOMER_ID"

MAX_HINT_KEYWORDS = 5  # 네이버 문서 기준 hintKeywords는 최대 5개


class SearchAdError(RuntimeError):
    pass


@dataclass
class RelatedKeyword:
    keyword: str
    monthly_pc: int
    monthly_mobile: int
    pc_is_low: bool = False       # "< 10"으로 온 경우 True (실제값은 0~9 중 하나, 정확히는 모름)
    mobile_is_low: bool = False

    @property
    def monthly_total(self) -> int:
        return self.monthly_pc + self.monthly_mobile


def _parse_count(raw) -> tuple[int, bool]:
    """"< 10" 같은 문자열과 숫자를 모두 정수로 정규화한다. 반환: (값, 저volume_여부)."""
    if isinstance(raw, (int, float)):
        return int(raw), False
    s = str(raw).strip()
    if s.startswith("<"):
        # "< 10"은 0~9 중 하나라는 뜻이지만 정확한 값은 API가 주지 않는다.
        # 순위를 매길 때 과대평가하지 않도록 하한인 0으로 둔다.
        return 0, True
    try:
        return int(float(s.replace(",", ""))), False
    except ValueError:
        return 0, False


class SearchAdClient:
    """검색광고 키워드도구 API 클라이언트 (stdlib만 사용, 일 단위 캐시)."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        secret_key: str | None = None,
        customer_id: str | None = None,
        cache_dir: str | None = ".cache/trendbot",
        timeout_sec: float = 10.0,
        rate_limit_sec: float = 0.2,
        max_retries: int = 3,
    ):
        self.api_key = api_key or os.environ.get(API_KEY_ENV, "")
        self.secret_key = secret_key or os.environ.get(SECRET_KEY_ENV, "")
        self.customer_id = customer_id or os.environ.get(CUSTOMER_ID_ENV, "")
        self.cache_dir = cache_dir
        self.timeout_sec = timeout_sec
        self.rate_limit_sec = rate_limit_sec
        self.max_retries = max_retries
        self._last_call = 0.0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def _require_credentials(self) -> None:
        if not (self.api_key and self.secret_key and self.customer_id):
            raise SearchAdError(
                "네이버 검색광고 API 인증 정보가 없습니다. 환경변수 3개를 모두 설정하세요.\n"
                f"  export {API_KEY_ENV}=액세스라이선스\n"
                f"  export {SECRET_KEY_ENV}=비밀키\n"
                f"  export {CUSTOMER_ID_ENV}=고객ID(CUSTOMER_ID)\n"
                "  (searchad.naver.com 로그인 → 도구 → API 사용 관리 → 서비스 신청)"
            )

    def _signature(self, timestamp: str, method: str, uri: str) -> str:
        message = f"{timestamp}.{method}.{uri}".encode("utf-8")
        digest = hmac.new(self.secret_key.encode("utf-8"), message, hashlib.sha256).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _headers(self, method: str, uri: str) -> dict[str, str]:
        timestamp = str(int(time.time() * 1000))
        return {
            "Content-Type": "application/json; charset=UTF-8",
            "X-Timestamp": timestamp,
            "X-API-KEY": self.api_key,
            "X-Customer": self.customer_id,
            "X-Signature": self._signature(timestamp, method, uri),
        }

    def _cache_path(self, url: str) -> str | None:
        if not self.cache_dir:
            return None
        from datetime import date

        h = hashlib.sha256(url.encode()).hexdigest()
        return os.path.join(self.cache_dir, f"searchad_{date.today().isoformat()}_{h}.json")

    def related_keywords(self, hint_keywords: list[str]) -> list[RelatedKeyword]:
        """연관 키워드 + 월간 검색량. 시드 키워드 자신도 결과에 포함될 수 있다."""
        self._require_credentials()
        hint_keywords = hint_keywords[:MAX_HINT_KEYWORDS]
        query = urllib.parse.urlencode({
            "hintKeywords": ",".join(hint_keywords),
            "showDetail": "1",
        })
        url = f"{API_HOST}{KEYWORDS_TOOL_URI}?{query}"

        cache_path = self._cache_path(url)
        if cache_path and os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                data = json.load(f)
        else:
            headers = self._headers("GET", KEYWORDS_TOOL_URI)
            last_err: Exception | None = None
            data = None
            delay = 1.0
            for attempt in range(self.max_retries):
                gap = time.monotonic() - self._last_call
                if gap < self.rate_limit_sec:
                    time.sleep(self.rate_limit_sec - gap)
                req = urllib.request.Request(url, headers=headers, method="GET")
                try:
                    with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    self._last_call = time.monotonic()
                    break
                except urllib.error.HTTPError as e:
                    detail = e.read().decode("utf-8", "replace")
                    if e.code in (401, 403):
                        raise SearchAdError(
                            f"HTTP {e.code} — 인증 실패입니다. API 키/비밀키/고객ID를 확인하세요.\n{detail}"
                        ) from e
                    if e.code == 429:
                        last_err = e
                    else:
                        raise SearchAdError(f"HTTP {e.code} {e.reason}: {detail}") from e
                except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                    last_err = e
                if attempt < self.max_retries - 1:
                    time.sleep(delay)
                    delay *= 2
            if data is None:
                raise SearchAdError(f"요청 실패({self.max_retries}회 재시도): {last_err}")
            if cache_path:
                with open(cache_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)

        out: list[RelatedKeyword] = []
        for item in data.get("keywordList", []):
            pc, pc_low = _parse_count(item.get("monthlyPcQcCnt", 0))
            mobile, mobile_low = _parse_count(item.get("monthlyMobileQcCnt", 0))
            out.append(RelatedKeyword(
                keyword=str(item.get("relKeyword", "")),
                monthly_pc=pc, monthly_mobile=mobile,
                pc_is_low=pc_low, mobile_is_low=mobile_low,
            ))
        return out


def rank_related(keywords: list[RelatedKeyword], *, top: int = 20) -> list[RelatedKeyword]:
    """검색량(PC+모바일) 합산 기준 내림차순 정렬 후 상위 N개."""
    return sorted(keywords, key=lambda k: k.monthly_total, reverse=True)[:top]
