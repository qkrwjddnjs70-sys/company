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


def scan_har(path: str, *, min_score: float = 3.0) -> list[HarHit]:
    """HAR에서 자막처럼 보이는 JSON 응답을 찾아 점수순으로 돌려준다."""
    with open(path, encoding="utf-8") as f:
        har = json.load(f)
    hits: list[HarHit] = []
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
        cands = find_array_candidates(data)
        if not cands or cands[0].score < min_score:
            continue
        headers = {}
        for h in req.get("headers", []):
            k = (h.get("name") or "").lower()
            if k in KEEP_HEADERS or k.startswith(KEEP_PREFIXES):
                headers[k] = h.get("value", "")
        hits.append(HarHit(
            url=req.get("url", ""), method=req.get("method", "GET"),
            status=resp.get("status", 0), candidates=cands[:3], headers=headers, body=data,
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


def build_config(
    url: str,
    headers: dict[str, str],
    candidate: ArrayCandidate | None,
    *,
    product_key: str | None = None,
    cookie_env: str = "HSMOA_DATAHUB_COOKIE",
    body: Any = None,
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

    return {
        "_note": [
            "F12 개발자도구 관측 결과로 자동 생성된 초안입니다.",
            "쿠키는 이 파일에 없습니다. 환경변수로 주입하세요:",
            f"  export {cookie_env}=\"$(cat .secrets/datahub.cookie)\"",
            "쿠키는 로그인 세션이라 만료됩니다. 401/403이 나면 F12에서 다시 복사하세요.",
        ],
        "base_url": f"{p.scheme}://{p.netloc}",
        "api_key_env": cookie_env,
        "auth": {"type": "cookie", "cookie_env": cookie_env, "extra_headers": safe_headers},
        "timeout_sec": 30.0,
        "max_retries": 4,
        "rate_limit_sec": 1.0,
        "cache_dir": ".cache/datahub",
        "endpoints": {"subtitle": {"path": path, "query": _templatize(query, product_key)}},
        "mapping": {"segments": seg, "broadcast": meta_map},
    }


def save_cookie(cookie: str, path: str = os.path.join(".secrets", "datahub.cookie")) -> str:
    """쿠키를 저장소에 커밋되지 않는 위치에 0600 권한으로 저장한다."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(cookie.strip() + "\n")
    os.chmod(path, 0o600)
    return path
