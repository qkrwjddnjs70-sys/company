"""브라우저 개발자도구(F12) 산출물에서 API 스펙을 역추적한다.

왜 필요한가
-----------
공식 API 문서 없이 DataHub 데이터를 쓰려면 두 가지가 필요하다.
  (1) 인증  — 로그인 세션 쿠키
  (2) 스펙  — 자막을 돌려주는 실제 XHR 주소와 응답 필드 구조
쿠키만으로는 (2)를 알 수 없다. F12 Network 탭이 (2)를 알려준다.

사용 흐름
---------
  1. 브라우저에서 자막 탭이 있는 방송 페이지를 연다
  2. F12 → Network → Fetch/XHR → 페이지 새로고침
  3-a. HAR 저장  ("Export HAR")  → hsbot devtools --har page.har     ← 권장(스키마까지 추론)
  3-b. 요청 우클릭 → Copy as cURL → hsbot devtools --curl req.txt    ← 엔드포인트만 파악

쿠키는 비밀번호와 같은 자격증명이다. 이 모듈은 쿠키를 설정 파일에 절대 쓰지 않고
.secrets/ 아래 별도 파일로 분리한 뒤 환경변수로 읽게 한다.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

# 브라우저가 붙이는 잡다한 헤더 중 재현에 실제로 필요한 것만 남긴다.
KEEP_HEADERS = {
    "cookie", "authorization", "user-agent", "referer", "accept",
    "accept-language", "origin", "content-type",
}
KEEP_PREFIXES = ("x-",)

TIME_KEY_HINTS = ("time", "sec", "start", "end", "offset", "ts", "datetime", "timestamp", "ms")
TEXT_KEY_HINTS = ("text", "subtitle", "caption", "content", "body", "message", "script", "sentence")
SPEAKER_KEY_HINTS = ("speaker", "host", "actor", "role")
NAME_KEY_HINTS = ("product_name", "prd_name", "goods_name", "name", "title", "item_name")
PRICE_KEY_HINTS = ("sale_price", "price", "amount", "cost")
CHANNEL_KEY_HINTS = ("channel_name", "tv_channel_name", "shop_name", "channel")

_HMS = re.compile(r"^(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")
_HANGUL = re.compile(r"[가-힣]")


# ---------------------------------------------------------------- cURL

@dataclass
class CurlRequest:
    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def parsed(self):
        return urllib.parse.urlparse(self.url)

    @property
    def base_url(self) -> str:
        p = self.parsed
        return f"{p.scheme}://{p.netloc}"

    @property
    def path(self) -> str:
        return self.parsed.path

    @property
    def query(self) -> dict[str, str]:
        return {k: v[0] for k, v in urllib.parse.parse_qs(self.parsed.query).items()}

    @property
    def cookie(self) -> str | None:
        return self.headers.get("cookie")

    def safe_headers(self) -> dict[str, str]:
        """쿠키/토큰을 뺀, 설정 파일에 적어도 되는 헤더."""
        return {
            k: v for k, v in self.headers.items()
            if k not in ("cookie", "authorization") and (k in KEEP_HEADERS or k.startswith(KEEP_PREFIXES))
        }


def parse_curl(text: str) -> CurlRequest:
    """DevTools의 'Copy as cURL (bash)' 출력을 파싱한다."""
    cleaned = text.replace("\\\n", " ").replace("^\n", " ").strip()
    if not cleaned.lstrip().startswith("curl"):
        raise ValueError("curl 명령이 아닙니다. 'Copy as cURL (bash)'로 복사한 내용을 넣어주세요.")
    try:
        argv = shlex.split(cleaned)
    except ValueError as e:
        raise ValueError(f"cURL 명령을 해석하지 못했습니다: {e}") from e

    url, method, headers = None, "GET", {}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in ("-H", "--header") and i + 1 < len(argv):
            raw = argv[i + 1]
            if ":" in raw:
                k, v = raw.split(":", 1)
                headers[k.strip().lower()] = v.strip()
            i += 2
        elif a in ("-b", "--cookie") and i + 1 < len(argv):
            headers["cookie"] = argv[i + 1]
            i += 2
        elif a in ("-X", "--request") and i + 1 < len(argv):
            method = argv[i + 1].upper()
            i += 2
        elif a in ("--data", "--data-raw", "-d", "--data-binary") and i + 1 < len(argv):
            method = "POST"
            i += 2
        elif a.startswith("-"):
            i += 2 if (i + 1 < len(argv) and not argv[i + 1].startswith("-")) and a not in (
                "--compressed", "-s", "-i", "-k", "-L", "--location", "-v"
            ) else 1
        else:
            if url is None:
                url = a
            i += 1
    if not url:
        raise ValueError("cURL 명령에서 URL을 찾지 못했습니다.")
    return CurlRequest(url=url, method=method, headers=headers)


# ---------------------------------------------------------------- 스키마 추론

@dataclass
class ArrayCandidate:
    """자막 배열일 가능성이 있는 위치."""

    path: str
    length: int
    keys: list[str]
    score: float
    text_keys: list[str] = field(default_factory=list)
    time_keys: list[str] = field(default_factory=list)
    speaker_keys: list[str] = field(default_factory=list)
    sample: dict[str, Any] = field(default_factory=dict)

    @property
    def start_keys(self) -> list[str]:
        """시작 시각 후보. 'end'가 들어간 키는 뒤로 뺀다 (앞 순서가 먼저 채택되므로)."""
        head = [k for k in self.time_keys if "end" not in k.lower()]
        return head or self.time_keys

    @property
    def end_keys(self) -> list[str]:
        return [k for k in self.time_keys if "end" in k.lower()]


def _looks_like_time(v: Any) -> bool:
    if isinstance(v, (int, float)):
        return 0 <= float(v) <= 90_000_000
    if isinstance(v, str):
        return bool(_HMS.match(v) or _ISO.match(v))
    return False


def _looks_like_utterance(v: Any) -> bool:
    return isinstance(v, str) and 2 <= len(v) <= 400 and bool(_HANGUL.search(v))


def find_array_candidates(obj: Any, *, min_len: int = 3) -> list[ArrayCandidate]:
    """JSON 어디에 자막 배열이 있는지 점수를 매겨 찾는다."""
    out: list[ArrayCandidate] = []

    def walk(node: Any, path: str, depth: int) -> None:
        if depth > 6:
            return
        if isinstance(node, list):
            dicts = [x for x in node if isinstance(x, dict)]
            if len(dicts) >= min_len:
                keys = sorted({k for d in dicts[:50] for k in d})
                text_keys, time_keys, spk_keys = [], [], []
                for k in keys:
                    vals = [d[k] for d in dicts[:50] if k in d]
                    if not vals:
                        continue
                    lk = k.lower()
                    text_ratio = sum(_looks_like_utterance(v) for v in vals) / len(vals)
                    time_ratio = sum(_looks_like_time(v) for v in vals) / len(vals)
                    if text_ratio >= 0.5 or any(h in lk for h in TEXT_KEY_HINTS):
                        text_keys.append(k)
                    if time_ratio >= 0.5 and any(h in lk for h in TIME_KEY_HINTS):
                        time_keys.append(k)
                    elif any(h in lk for h in TIME_KEY_HINTS) and time_ratio >= 0.3:
                        time_keys.append(k)
                    elif lk == "t" and time_ratio >= 0.5:
                        # "t" 는 substring 힌트로 잡히지 않는다 (다른 키에 흔한 글자라
                        # TIME_KEY_HINTS 에 넣으면 오탐이 늘어난다). 정확히 일치할 때만 인정.
                        time_keys.append(k)
                    if any(h in lk for h in SPEAKER_KEY_HINTS):
                        spk_keys.append(k)
                # 화자 컬럼도 한글 문자열이라 본문으로 오인된다. 명시적으로 제외한다.
                text_keys = [k for k in text_keys if k not in spk_keys]
                # 이름에 힌트가 있는 키를 앞에 둔다 (앞 순서가 먼저 채택된다).
                text_keys.sort(key=lambda k: (not any(h in k.lower() for h in TEXT_KEY_HINTS), k))
                score = (
                    3.0 * bool(text_keys) + 2.0 * bool(time_keys) + 0.5 * bool(spk_keys)
                    + min(len(dicts) / 100.0, 2.0)
                )
                # 상품 목록은 자막과 겉모습이 비슷하다(한글 문자열 + 시각 필드).
                # 목록 증거가 강하면 자막 점수를 깎아 오인을 막는다.
                score -= 5.0 * listing_evidence(dicts)
                if score > 0:
                    out.append(ArrayCandidate(
                        path=path or "(root)", length=len(dicts), keys=keys, score=round(score, 2),
                        text_keys=text_keys, time_keys=sorted(time_keys), speaker_keys=spk_keys,
                        sample=dicts[0],
                    ))
            for i, x in enumerate(node[:3]):
                walk(x, f"{path}.{i}" if path else str(i), depth + 1)
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k, depth + 1)

    walk(obj, "", 0)
    out.sort(key=lambda c: -c.score)
    return out


def find_meta_paths(obj: Any, *, skip_prefix: str = "") -> dict[str, list[str]]:
    """응답에서 상품명·가격·채널명이 있을 법한 경로를 찾는다.

    자막 배열(skip_prefix) 내부는 건너뛴다. 그 안의 name/price는 상품 메타가 아니다.
    """
    found: dict[str, list[tuple[int, str]]] = {"name": [], "price": [], "channel": []}

    def walk(node: Any, path: str, depth: int) -> None:
        if depth > 6 or (skip_prefix and path.startswith(skip_prefix)):
            return
        if isinstance(node, dict):
            for k, v in node.items():
                sub = f"{path}.{k}" if path else k
                lk = k.lower()
                if isinstance(v, str) and _HANGUL.search(v) and len(v) <= 120:
                    for i, h in enumerate(NAME_KEY_HINTS):
                        if h in lk:
                            found["name"].append((i, sub))
                            break
                    for i, h in enumerate(CHANNEL_KEY_HINTS):
                        if h in lk:
                            found["channel"].append((i, sub))
                            break
                elif isinstance(v, (int, float)) and 100 <= float(v) <= 10 ** 9:
                    for i, h in enumerate(PRICE_KEY_HINTS):
                        if h in lk:
                            found["price"].append((i, sub))
                            break
                walk(v, sub, depth + 1)
        elif isinstance(node, list):
            for i, x in enumerate(node[:2]):
                walk(x, f"{path}.{i}" if path else str(i), depth + 1)

    walk(obj, "", 0)
    # 힌트 우선순위(구체적인 이름이 먼저) → 경로가 짧은 순
    return {
        k: [p for _, p in sorted(v, key=lambda t: (t[0], t[1].count("."), t[1]))]
        for k, v in found.items()
    }


# ---------------------------------------------------------------- 검색 응답 추론

KEY_KEY_HINTS = ("product_key", "productkey", "prd_key", "goods_id", "product_id", "prd_no", "id", "key")
START_KEY_HINTS = ("start_datetime", "start_time", "starttime", "broadcast_start", "onair_start",
                   "air_start", "start", "begin")
END_KEY_HINTS = ("end_datetime", "end_time", "endtime", "broadcast_end", "onair_end", "air_end", "end")
URL_KEY_HINTS = ("url", "link", "href", "detail_url", "page_url")
IMAGE_KEY_HINTS = ("thumb", "image", "img", "photo", "poster", "banner", "icon")


@dataclass
class ListingCandidate:
    """'방송 목록'(검색 결과)일 가능성이 있는 배열 위치.

    자막 배열과 겉모습이 비슷해 헷갈리기 쉽다. 결정적 차이는 두 가지다.
      · 자막 항목 = 짧은 발화 + 상대시각(00:05:12)  → 한 방송 안의 내용
      · 목록 항목 = 상품명 + 절대시각(2026-09-04T20:38) + 가격 + 상품키
    """

    path: str
    length: int
    keys: list[str]
    score: float
    key_keys: list[str] = field(default_factory=list)
    name_keys: list[str] = field(default_factory=list)
    channel_keys: list[str] = field(default_factory=list)
    channel_name_keys: list[str] = field(default_factory=list)
    start_keys: list[str] = field(default_factory=list)
    end_keys: list[str] = field(default_factory=list)
    price_keys: list[str] = field(default_factory=list)
    url_keys: list[str] = field(default_factory=list)
    sample: dict[str, Any] = field(default_factory=dict)


def _looks_like_abs_time(v: Any) -> bool:
    return isinstance(v, str) and bool(_ISO.match(v))


def _hinted(key: str, hints: tuple[str, ...]) -> bool:
    lk = key.lower().replace("-", "_")
    return any(h in lk for h in hints)


@dataclass
class FieldProfile:
    """배열 안 한 필드의 '값 생김새' 요약.

    필드명에만 기대면 `goodsNm`, `prdKey`, `shopNm` 처럼 줄임말을 쓰는 실제
    커머스 API에서 곧바로 실패한다. 그래서 이름은 동점 처리에만 쓰고,
    판정은 값의 모양으로 한다.
    """

    key: str
    n: int
    unique_ratio: float = 0.0
    hangul_ratio: float = 0.0
    abs_time_ratio: float = 0.0
    numeric_ratio: float = 0.0
    url_ratio: float = 0.0
    avg_len: float = 0.0
    avg_num: float = 0.0
    min_value: Any = None


def profile_fields(dicts: list[dict], *, sample: int = 50) -> dict[str, FieldProfile]:
    """배열 항목들을 훑어 필드별 값 통계를 만든다."""
    rows = dicts[:sample]
    keys = sorted({k for d in rows for k in d})
    out: dict[str, FieldProfile] = {}
    for k in keys:
        vals = [d[k] for d in rows if k in d and d[k] not in (None, "")]
        if not vals:
            continue
        n = len(vals)
        strs = [v for v in vals if isinstance(v, str)]
        nums = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
        hashable = [v for v in vals if isinstance(v, (str, int, float, bool))]
        out[k] = FieldProfile(
            key=k,
            n=n,
            unique_ratio=(len(set(hashable)) / len(hashable)) if hashable else 0.0,
            hangul_ratio=sum(bool(_HANGUL.search(s)) for s in strs) / n,
            abs_time_ratio=sum(_looks_like_abs_time(v) for v in vals) / n,
            numeric_ratio=len(nums) / n,
            url_ratio=sum(("://" in s or s.startswith("/")) for s in strs) / n,
            avg_len=(sum(len(s) for s in strs) / len(strs)) if strs else 0.0,
            avg_num=(sum(nums) / len(nums)) if nums else 0.0,
            min_value=min(strs) if strs else None,
        )
    return out


def _listing_buckets(profiles: dict[str, FieldProfile]) -> dict[str, list[str]]:
    """값 생김새로 상품키·상품명·채널·시각·가격·URL 필드를 고른다."""
    b: dict[str, list[str]] = {
        "key": [], "name": [], "channel": [], "channel_name": [],
        "start": [], "end": [], "price": [], "url": [],
    }

    # --- 시각: 절대시각 필드를 모아 값이 이른 쪽을 start, 늦은 쪽을 end 로 본다
    times = [p for p in profiles.values() if p.abs_time_ratio >= 0.6]
    if len(times) >= 2:
        times.sort(key=lambda p: (str(p.min_value or "")))
        b["start"] = [times[0].key]
        b["end"] = [t.key for t in times[1:]]
    elif times:
        b["start"] = [times[0].key]
    # 이름 힌트가 값 판정과 어긋나면 이름 쪽을 믿는다 (end 를 start 로 뽑는 실수 방지)
    hinted_start = [p.key for p in times if _hinted(p.key, START_KEY_HINTS)
                    and not _hinted(p.key, END_KEY_HINTS)]
    hinted_end = [p.key for p in times if _hinted(p.key, END_KEY_HINTS)]
    if hinted_start:
        b["start"] = hinted_start
    if hinted_end:
        b["end"] = hinted_end

    # --- URL: 이미지 URL(썸네일)은 상세 페이지가 아니므로 제외한다
    b["url"] = [
        p.key for p in profiles.values()
        if p.url_ratio >= 0.6 and not _hinted(p.key, IMAGE_KEY_HINTS)
    ]
    b["url"].sort(key=lambda k: (not _hinted(k, URL_KEY_HINTS), k))

    # --- 상품키: 행마다 거의 고유하고 숫자를 포함하는 짧은 값
    for p in profiles.values():
        if p.key in b["url"]:
            continue
        looks_id = p.unique_ratio >= 0.8 and (
            (p.numeric_ratio >= 0.8 and p.avg_num > 999)
            or (0 < p.avg_len <= 64 and p.hangul_ratio < 0.2 and p.abs_time_ratio < 0.5)
        )
        if looks_id and (p.numeric_ratio >= 0.8 or _hinted(p.key, KEY_KEY_HINTS)):
            b["key"].append(p.key)
    b["key"].sort(key=lambda k: (not _hinted(k, KEY_KEY_HINTS), k))

    # --- 한글 텍스트 필드를 길이로 나눈다: 긴 쪽이 상품명, 짧고 반복되면 채널명
    texts = [p for p in profiles.values()
             if p.hangul_ratio >= 0.5 and p.avg_len >= 2 and p.abs_time_ratio < 0.5]
    texts.sort(key=lambda p: -p.avg_len)
    for p in texts:
        short_and_repeated = p.avg_len <= 14 and p.unique_ratio <= 0.6
        if short_and_repeated and not _hinted(p.key, NAME_KEY_HINTS[:3]):
            b["channel_name"].append(p.key)
        elif p.avg_len >= 4:
            b["name"].append(p.key)
    b["name"].sort(key=lambda k: (not _hinted(k, NAME_KEY_HINTS), k))

    # --- 채널 코드: 짧고 반복되는 비한글 문자열 (상품키/URL 로 이미 쓴 건 제외)
    used = set(b["key"]) | set(b["url"]) | set(b["name"]) | set(b["channel_name"])
    for p in profiles.values():
        if p.key in used or p.abs_time_ratio >= 0.5:
            continue
        if 0 < p.avg_len <= 20 and p.hangul_ratio < 0.5 and p.unique_ratio <= 0.6:
            b["channel"].append(p.key)
    b["channel"].sort(key=lambda k: (not _hinted(k, CHANNEL_KEY_HINTS + ("shop", "chan")), k))

    # --- 가격: 100 이상인 수치 필드. 여러 개면 평균이 작은 쪽(=판매가)을 먼저.
    prices = [p for p in profiles.values()
              if p.numeric_ratio >= 0.6 and p.avg_num >= 100 and p.key not in b["key"]]
    prices.sort(key=lambda p: (not _hinted(p.key, ("sale", "판매")), p.avg_num))
    b["price"] = [p.key for p in prices]

    return b


def find_listing_candidates(obj: Any, *, min_len: int = 2, min_score: float = 5.0) -> list[ListingCandidate]:
    """JSON 어디에 '방송/상품 목록'이 있는지 점수를 매겨 찾는다."""
    out: list[ListingCandidate] = []

    def walk(node: Any, path: str, depth: int) -> None:
        if depth > 6:
            return
        if isinstance(node, list):
            dicts = [x for x in node if isinstance(x, dict)]
            if len(dicts) >= min_len:
                profiles = profile_fields(dicts)
                b = _listing_buckets(profiles)
                score = (
                    3.0 * bool(b["key"])
                    + 3.0 * bool(b["name"])
                    + 2.5 * bool(b["start"])
                    + 1.5 * bool(b["price"])
                    + 1.0 * bool(b["channel"] or b["channel_name"])
                    + 0.5 * bool(b["url"])
                    + min(len(dicts) / 50.0, 1.0)
                )
                # 상품명이 없거나, 상품키·URL 둘 다 없으면 목록이 아니다.
                if b["name"] and (b["key"] or b["url"]) and score >= min_score:
                    out.append(ListingCandidate(
                        path=path or "(root)", length=len(dicts),
                        keys=sorted(profiles), score=round(score, 2),
                        key_keys=b["key"][:4], name_keys=b["name"][:4],
                        channel_keys=b["channel"][:4], channel_name_keys=b["channel_name"][:4],
                        start_keys=b["start"][:4], end_keys=b["end"][:4],
                        price_keys=b["price"][:4], url_keys=b["url"][:4],
                        sample=dicts[0],
                    ))
            for i, x in enumerate(node[:3]):
                walk(x, f"{path}.{i}" if path else str(i), depth + 1)
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k, depth + 1)

    walk(obj, "", 0)
    out.sort(key=lambda c: -c.score)
    return out


def listing_evidence(dicts: list[dict]) -> float:
    """이 배열이 '자막'이 아니라 '목록'이라는 증거의 세기 (0~1).

    자막 탐지기가 상품 목록을 자막으로 오인하는 것을 막는 데 쓴다.
    목록에만 있는 특징은 절대시각 · 가격 · 행마다 고유한 상품키 · 상세 URL 이다.
    """
    if len(dicts) < 2:
        return 0.0
    profiles = profile_fields(dicts)
    if not profiles:
        return 0.0
    b = _listing_buckets(profiles)
    signals = [bool(b["start"]), bool(b["price"]), bool(b["key"]), bool(b["url"]), bool(b["name"])]
    return sum(signals) / len(signals)


# ---------------------------------------------------------------- HAR

@dataclass
class HarHit:
    url: str
    method: str
    status: int
    candidates: list[ArrayCandidate]
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None

    @property
    def score(self) -> float:
        return self.candidates[0].score if self.candidates else 0.0


@dataclass
class SearchHit:
    """검색(방송 목록) 응답으로 보이는 HAR 항목."""

    url: str
    method: str
    status: int
    candidates: list[ListingCandidate]
    headers: dict[str, str] = field(default_factory=dict)
    body: Any = None

    @property
    def score(self) -> float:
        return self.candidates[0].score if self.candidates else 0.0

    @property
    def query(self) -> dict[str, str]:
        return {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlparse(self.url).query).items()}

    def guess_keyword(self) -> str | None:
        """어떤 쿼리 파라미터가 검색어였는지 되짚는다 (경로 템플릿화에 쓴다)."""
        for k, v in self.query.items():
            if _hinted(k, ("keyword", "query", "search", "q", "word", "term")) and v:
                return v
        return None


def _iter_har_json(path: str):
    """HAR에서 JSON 응답만 훑는다. 파일을 한 번만 읽기 위해 분리했다."""
    with open(path, encoding="utf-8") as f:
        har = json.load(f)
    for entry in har.get("log", {}).get("entries", []):
        req, resp = entry.get("request", {}), entry.get("response", {})
        content = resp.get("content", {}) or {}
        text = content.get("text")
        if not text or content.get("encoding") == "base64":
            continue
        mime = (content.get("mimeType") or "").lower()
        if "json" not in mime and not text.lstrip().startswith(("{", "[")):
            continue
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue
        headers = {}
        for h in req.get("headers", []):
            k = (h.get("name") or "").lower()
            if k in KEEP_HEADERS or k.startswith(KEEP_PREFIXES):
                headers[k] = h.get("value", "")
        yield req.get("url", ""), req.get("method", "GET"), resp.get("status", 0), headers, data


def scan_har(path: str, *, min_score: float = 3.0) -> list[HarHit]:
    """HAR에서 자막처럼 보이는 JSON 응답을 찾아 점수순으로 돌려준다."""
    hits: list[HarHit] = []
    for url, method, status, headers, data in _iter_har_json(path):
        cands = find_array_candidates(data)
        if not cands or cands[0].score < min_score:
            continue
        hits.append(HarHit(
            url=url, method=method, status=status,
            candidates=cands[:3], headers=headers, body=data,
        ))
    hits.sort(key=lambda h: -h.score)
    return hits


def scan_har_search(path: str, *, min_score: float = 5.0) -> list[SearchHit]:
    """HAR에서 '방송 목록'처럼 보이는 JSON 응답을 찾는다.

    자막 응답과 겹칠 수 있으므로(한 응답에 목록과 자막이 같이 오는 경우도 있다)
    두 스캔은 서로 배타적이지 않다. 판단은 호출 측에서 점수로 한다.
    """
    hits: list[SearchHit] = []
    for url, method, status, headers, data in _iter_har_json(path):
        cands = find_listing_candidates(data)
        if not cands or cands[0].score < min_score:
            continue
        hits.append(SearchHit(
            url=url, method=method, status=status,
            candidates=cands[:3], headers=headers, body=data,
        ))
    hits.sort(key=lambda h: -h.score)
    return hits


# ---------------------------------------------------------------- 설정 생성

def _templatize(query: dict[str, str], product_key: str | None) -> dict[str, str]:
    """관측된 쿼리값을 hsbot 파라미터 템플릿으로 바꾼다."""
    out: dict[str, str] = {}
    for k, v in query.items():
        lk = k.lower()
        if "start" in lk:
            out[k] = "{start_datetime}"
        elif "end" in lk:
            out[k] = "{end_datetime}"
        elif "channel" in lk:
            out[k] = "{tv_channel}"
        elif product_key and v == product_key:
            out[k] = "{product_key}"
        elif lk in ("tab",):
            out[k] = v
        else:
            out[k] = v
    return out


def _templatize_search(query: dict[str, str], keyword: str | None) -> dict[str, str]:
    """검색 요청의 쿼리를 템플릿화한다.

    관측된 검색어 자리에는 `{keyword}`, 페이지/개수 자리에는 `{page}` `{size}` 가 들어간다.
    나머지(정렬, 카테고리 필터 등)는 관측값 그대로 둔다 — 브라우저가 보낸 값을
    그대로 재현하는 편이 서버가 거부할 확률이 낮다.
    """
    out: dict[str, str] = {}
    for k, v in query.items():
        if keyword and v == keyword:
            out[k] = "{keyword}"
        elif _hinted(k, ("keyword", "query", "search", "word", "term")) or k.lower() == "q":
            out[k] = "{keyword}"
        # size 를 page 보다 먼저 본다. 'pageSize' 는 'page' 를 포함하므로
        # 순서를 뒤집으면 개수 파라미터가 페이지 번호로 잘못 잡힌다.
        elif _hinted(k, ("size", "limit", "count", "per_page", "perpage", "rows")) and str(v).isdigit():
            out[k] = "{size}"
        elif _hinted(k, ("page", "pageno", "page_no", "offset")) and str(v).isdigit():
            out[k] = "{page}"
        elif "start" in k.lower() and _ISO.match(str(v)):
            out[k] = "{start_datetime}"
        elif "end" in k.lower() and _ISO.match(str(v)):
            out[k] = "{end_datetime}"
        else:
            out[k] = v
    return out


def _first_or(keys: list[str], fallback: list[str]) -> list[str]:
    return keys[:4] if keys else fallback


def build_search_section(hit: "SearchHit") -> dict[str, Any]:
    """검색 응답 관측 결과 → config 의 endpoints.search / mapping.search 조각."""
    c = hit.candidates[0]
    p = urllib.parse.urlparse(hit.url)
    query = {k: v[0] for k, v in urllib.parse.parse_qs(p.query).items()}
    kw = hit.guess_keyword()

    list_path = "" if c.path == "(root)" else c.path
    return {
        "endpoint": {"path": p.path, "query": _templatize_search(query, kw)},
        "mapping": {
            "list_paths": [list_path],
            "product_key_paths": _first_or(c.key_keys, ["product_key", "id"]),
            "product_name_paths": _first_or(c.name_keys, ["product_name", "name", "title"]),
            "channel_paths": _first_or(c.channel_keys, ["channel", "tv_channel"]),
            "channel_name_paths": _first_or(c.channel_name_keys, ["channel_name", "shop_name"]),
            "start_paths": _first_or(c.start_keys, ["start_datetime", "start_time"]),
            "end_paths": _first_or(c.end_keys, ["end_datetime", "end_time"]),
            "price_paths": _first_or(c.price_keys, ["sale_price", "price"]),
            "url_paths": _first_or(c.url_keys, ["url", "link"]),
        },
        "observed_keyword": kw,
    }


def build_config(
    url: str,
    headers: dict[str, str],
    candidate: ArrayCandidate | None,
    *,
    product_key: str | None = None,
    cookie_env: str = "HSMOA_DATAHUB_COOKIE",
    body: Any = None,
    search_hit: "SearchHit | None" = None,
) -> dict[str, Any]:
    """관측 결과 → config/datahub.json 초안. 쿠키 값은 절대 넣지 않는다."""
    p = urllib.parse.urlparse(url)
    query = {k: v[0] for k, v in urllib.parse.parse_qs(p.query).items()}
    path = p.path
    if product_key and product_key in path:
        path = path.replace(product_key, "{product_key}")

    safe_headers = {
        k: v for k, v in headers.items()
        if k not in ("cookie", "authorization") and (k in KEEP_HEADERS or k.startswith(KEEP_PREFIXES))
    }
    safe_headers.pop("accept", None)   # 클라이언트가 직접 붙인다

    seg: dict[str, Any] = {
        "list_paths": [candidate.path] if candidate and candidate.path != "(root)" else ["(root)"],
        "text_paths": candidate.text_keys if candidate else ["text"],
        "start_paths": candidate.start_keys if candidate else ["start_time"],
        "end_paths": (candidate.end_keys if candidate else []) or ["end_time"],
        "speaker_paths": (candidate.speaker_keys if candidate else []) or ["speaker"],
    }
    if seg["list_paths"] == ["(root)"]:
        seg["list_paths"] = [""]

    meta = find_meta_paths(body, skip_prefix=(candidate.path if candidate else "")) if body is not None else {}
    meta_map = {
        "product_name_paths": (meta.get("name") or [])[:4]
        or ["data.product_name", "data.name", "product_name", "name", "title"],
        "price_paths": (meta.get("price") or [])[:4] or ["data.price", "data.sale_price", "price"],
        "channel_name_paths": (meta.get("channel") or [])[:4] or ["data.channel_name", "channel_name"],
    }

    note = [
        "F12 개발자도구 관측 결과로 자동 생성된 초안입니다.",
        "쿠키는 이 파일에 없습니다. 환경변수로 주입하세요:",
        f"  export {cookie_env}=\"$(cat .secrets/datahub.cookie)\"",
        "쿠키는 로그인 세션이라 만료됩니다. 401/403이 나면 F12에서 다시 복사하세요.",
    ]
    endpoints: dict[str, Any] = {}
    mapping: dict[str, Any] = {"segments": seg, "broadcast": meta_map}
    if candidate is not None or not search_hit:
        endpoints["subtitle"] = {"path": path, "query": _templatize(query, product_key)}

    if search_hit is not None:
        sec = build_search_section(search_hit)
        endpoints["search"] = sec["endpoint"]
        mapping["search"] = sec["mapping"]
        sp = urllib.parse.urlparse(search_hit.url)
        if f"{sp.scheme}://{sp.netloc}" != f"{p.scheme}://{p.netloc}":
            note.append(
                f"※ 검색과 자막의 호스트가 다릅니다({sp.netloc} vs {p.netloc}). "
                "base_url 은 자막 기준이므로 endpoints.search.path 를 절대 URL로 바꾸세요."
            )
        if sec["observed_keyword"]:
            note.append(f"검색어로 관측된 값: {sec['observed_keyword']!r} → '{{keyword}}' 로 템플릿화했습니다.")

    return {
        "_note": note,
        "base_url": f"{p.scheme}://{p.netloc}",
        "api_key_env": cookie_env,
        "auth": {"type": "cookie", "cookie_env": cookie_env, "extra_headers": safe_headers},
        "timeout_sec": 30.0,
        "max_retries": 4,
        "rate_limit_sec": 1.0,
        "cache_dir": ".cache/datahub",
        "endpoints": endpoints,
        "mapping": mapping,
    }


def extract_refs(hit: "SearchHit") -> list:
    """HAR의 검색 응답 본문에서 곧바로 방송 목록을 뽑는다 (네트워크 불필요).

    HAR에는 응답 본문이 통째로 들어 있으므로, 브라우저에서 '로보락'을 한 번
    검색해두면 그 결과 목록을 요청 0회로 그대로 쓸 수 있다.
    """
    from .datahub import DataHubClient, DataHubConfig   # 순환 import 회피

    if not hit.candidates:
        raise ValueError("이 응답에서는 방송 목록을 찾지 못했습니다.")
    sec = build_search_section(hit)
    p = urllib.parse.urlparse(hit.url)
    cfg = DataHubConfig(
        base_url=f"{p.scheme}://{p.netloc}",
        endpoints={"search": sec["endpoint"]},
        mapping={"search": sec["mapping"]},
        auth={},
        cache_dir=None,
    )
    return DataHubClient(cfg, api_key="unused").to_refs(hit.body)


def save_cookie(cookie: str, path: str = os.path.join(".secrets", "datahub.cookie")) -> str:
    """쿠키를 저장소에 커밋되지 않는 위치에 0600 권한으로 저장한다."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(cookie.strip() + "\n")
    os.chmod(path, 0o600)
    return path


# ---------------------------------------------------------------- HAR 직접 추출

def extract_broadcast(
    hit: "HarHit",
    *,
    channel: str | None = None,
    channel_name: str | None = None,
    product_name: str | None = None,
    start_datetime: str | None = None,
    end_datetime: str | None = None,
):
    """HAR 응답 본문에서 곧바로 Broadcast를 만든다.

    HAR에는 응답 본문이 통째로 들어 있으므로 네트워크 요청 없이 분석할 수 있다.
    시작/종료 시각과 상품키는 관측된 요청 URL의 쿼리에서 최대한 유추한다.
    """
    from .datahub import DataHubClient, DataHubConfig   # 순환 import 회피

    if not hit.candidates:
        raise ValueError("이 응답에서는 자막 배열을 찾지 못했습니다.")

    p = urllib.parse.urlparse(hit.url)
    query = {k: v[0] for k, v in urllib.parse.parse_qs(p.query).items()}
    segs = [x for x in p.path.split("/") if x]
    product_key = next((x for x in reversed(segs) if "_" in x or x.isdigit()), segs[-1] if segs else "unknown")

    def pick(*names: str) -> str | None:
        for k, v in query.items():
            lk = k.lower()
            if any(n in lk for n in names):
                return v
        return None

    start = start_datetime or pick("start") 
    end = end_datetime or pick("end")
    if not start:
        raise ValueError(
            "방송 시작시각을 알 수 없습니다. --start 로 직접 넘겨주세요 "
            "(예: --start 2026-09-04T20:38:00+09:00)"
        )
    if not end:
        end = start   # duration은 자막 마지막 시각으로 대체된다

    cfg_dict = build_config(hit.url, hit.headers, hit.candidates[0], product_key=product_key, body=hit.body)
    cfg_dict.pop("_note", None)
    cfg_dict["cache_dir"] = None
    client = DataHubClient(DataHubConfig(**cfg_dict), api_key="unused")

    bc = client.to_broadcast(
        hit.body, hit.body,
        product_key=product_key,
        start_datetime=start,
        end_datetime=end,
        channel=channel or (product_key.split("_")[0] if "_" in product_key else product_key),
        product_name=product_name,
    )
    if channel_name:
        bc.channel_name = channel_name
    bc.source = "har"
    bc.extra["har_url"] = hit.url
    return bc
