"""홈쇼핑모아 DataHub API 어댑터.

중요
----
DataHub 공식 API 스펙(엔드포인트 경로, 응답 필드명, 자막 제공 여부)은
이 코드 작성 시점에 확인하지 못했다. 그래서 경로/필드명을 코드에 박지 않고
전부 JSON 설정으로 뺐다. 실제 스펙을 받으면 config/datahub.json만 고치면 된다.

설정 구조는 config/datahub.example.json 참고.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..discover import BroadcastRef
from ..models import Broadcast, Segment, KST
from .base import dig, first_found, first_present, to_seconds

DEFAULT_CONFIG_PATH = os.path.join("config", "datahub.json")

_MISSING = object()   # "경로 없음"과 "빈 배열"을 구분하기 위한 센티넬


def _as_str(v: Any) -> str | None:
    return None if v in (None, "") else str(v)


class DataHubError(RuntimeError):
    pass


@dataclass
class DataHubConfig:
    base_url: str
    endpoints: dict[str, dict[str, Any]]
    mapping: dict[str, Any]
    auth: dict[str, Any] = field(default_factory=dict)
    api_key_env: str = "HSMOA_DATAHUB_API_KEY"
    timeout_sec: float = 30.0
    max_retries: int = 4
    rate_limit_sec: float = 0.5
    cache_dir: str | None = ".cache/datahub"

    @classmethod
    def load(cls, path: str | None = None) -> "DataHubConfig":
        path = path or os.environ.get("HSMOA_DATAHUB_CONFIG") or DEFAULT_CONFIG_PATH
        if not os.path.exists(path):
            example = os.path.join("config", "datahub.example.json")
            raise DataHubError(
                f"설정 파일이 없습니다: {path}\n"
                f"  · 공식 API 문서가 없다면: 브라우저에서 검색·자막을 한 번 눌러본 뒤 HAR을 저장하고\n"
                f"    `python3 -m hsbot devtools --har page.har` 로 자동 생성하세요(권장).\n"
                f"  · 문서를 받았다면: {example} 를 복사해 실제 스펙에 맞게 고치세요."
            )
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        raw.pop("_comment", None)
        raw.pop("_note", None)
        return cls(**raw)


class DataHubClient:
    """설정 주도(config-driven) HTTP 클라이언트. stdlib만 사용."""

    def __init__(self, config: DataHubConfig | None = None, *, api_key: str | None = None):
        self.cfg = config or DataHubConfig.load()
        env_name = (self.cfg.auth or {}).get("cookie_env") or self.cfg.api_key_env
        self.api_key = api_key or os.environ.get(env_name, "")
        self._credential_env = env_name
        self._last_call = 0.0
        if self.cfg.cache_dir:
            os.makedirs(self.cfg.cache_dir, exist_ok=True)

    # ---------- 저수준 ----------
    @staticmethod
    def _canon(key: str) -> str:
        """HTTP 헤더 키를 Title-Case로 통일한다.

        urllib은 키를 capitalize()로 정규화하므로 'user-agent'와 'User-Agent'가
        같은 헤더로 충돌한다. 미리 통일해 설정 값이 명확히 기본값을 덮게 한다.
        """
        return "-".join(w.capitalize() for w in key.split("-"))

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json", "User-Agent": "hsbot/0.1"}
        auth = self.cfg.auth or {}
        kind = auth.get("type")
        if kind == "header":
            self._require_credential("API 키")
            h[auth.get("header", "Authorization")] = auth.get(
                "value_template", "Bearer {api_key}"
            ).format(api_key=self.api_key)
        elif kind == "cookie":
            # 브라우저 로그인 세션을 그대로 쓰는 경로. 쿠키는 만료되므로
            # 401/403이 나면 F12에서 다시 복사해야 한다.
            self._require_credential("로그인 쿠키")
            h["Cookie"] = self.api_key
        for k, v in (auth.get("extra_headers", {}) or {}).items():
            h[self._canon(k)] = v
        return h

    def _require_credential(self, what: str) -> None:
        if not self.api_key:
            raise DataHubError(
                f"{what}이(가) 없습니다. 환경변수 {self._credential_env} 를 설정하세요.\n"
                f'  예) export {self._credential_env}="$(cat .secrets/datahub.cookie)"'
            )

    @staticmethod
    def _fmt(tmpl: str, params: dict[str, Any], *, where: str) -> str:
        """템플릿을 채운다. 없는 파라미터는 조용히 넘기지 않고 알려준다."""
        try:
            return str(tmpl).format(**params)
        except KeyError as e:
            raise DataHubError(
                f"{where} 의 템플릿 {tmpl!r} 에 있는 {e} 를 채울 값이 없습니다.\n"
                f"  사용 가능한 값: {sorted(params)}"
            ) from e

    def _url(self, name: str, params: dict[str, Any]) -> str:
        ep = self.cfg.endpoints.get(name)
        if not ep:
            raise DataHubError(
                f"설정에 '{name}' 엔드포인트가 없습니다.\n"
                f"  현재 설정된 엔드포인트: {sorted(self.cfg.endpoints)}\n"
                f"  → F12에서 해당 요청을 관측한 HAR로 "
                f"`hsbot devtools --har ... --force` 를 다시 돌리면 추가됩니다."
            )
        path = self._fmt(ep["path"], params, where=f"endpoints.{name}.path")
        query = {}
        for k, tmpl in (ep.get("query") or {}).items():
            v = self._fmt(tmpl, params, where=f"endpoints.{name}.query.{k}")
            if v and v != "None":
                query[k] = v
        auth = self.cfg.auth or {}
        if auth.get("type") == "query":
            query[auth.get("param", "api_key")] = self.api_key
        url = urllib.parse.urljoin(self.cfg.base_url.rstrip("/") + "/", path.lstrip("/"))
        return f"{url}?{urllib.parse.urlencode(query)}" if query else url

    def _cache_path(self, url: str) -> str | None:
        if not self.cfg.cache_dir:
            return None
        import hashlib

        return os.path.join(self.cfg.cache_dir, hashlib.sha256(url.encode()).hexdigest() + ".json")

    def get(self, name: str, *, use_cache: bool = True, **params) -> Any:
        url = self._url(name, params)
        method = (self.cfg.endpoints.get(name) or {}).get("method", "GET").upper()
        # POST라도 응답이 안정적이면(자막처럼 broadcast_id로 고정된 리소스) 캐시를 쓴다.
        cp = self._cache_path(url)
        if use_cache and cp and os.path.exists(cp):
            with open(cp, encoding="utf-8") as f:
                return json.load(f)

        delay = 2.0
        last_err: Exception | None = None
        for attempt in range(self.cfg.max_retries):
            gap = time.monotonic() - self._last_call
            if gap < self.cfg.rate_limit_sec:
                time.sleep(self.cfg.rate_limit_sec - gap)
            try:
                body = b"" if method != "GET" else None
                req = urllib.request.Request(url, data=body, headers=self._headers(), method=method)
                with urllib.request.urlopen(req, timeout=self.cfg.timeout_sec) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self._last_call = time.monotonic()
                if cp:
                    with open(cp, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                return data
            except urllib.error.HTTPError as e:
                # 4xx는 재시도해도 소용없다 (429 제외)
                if e.code in (401, 403):
                    raise DataHubError(
                        f"HTTP {e.code} {e.reason} — 인증 실패입니다.\n"
                        f"  쿠키 인증이라면 세션이 만료됐을 가능성이 큽니다. "
                        f"F12에서 다시 복사한 뒤 {self._credential_env} 를 갱신하세요.\n"
                        f"  요청: {url}"
                    ) from e
                if e.code != 429 and 400 <= e.code < 500:
                    raise DataHubError(f"HTTP {e.code} {e.reason} — {url}") from e
                last_err = e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
            if attempt < self.cfg.max_retries - 1:
                time.sleep(delay)
                delay *= 2
        raise DataHubError(f"요청 실패({self.cfg.max_retries}회 재시도): {url}\n  마지막 오류: {last_err}")

    # ---------- 설정 점검 ----------
    _FLAG_PREFIXES = ("is_", "has_", "use_", "enable_", "with_", "include_", "no_")

    def _check_search_query(self) -> None:
        """검색 쿼리에 명백히 잘못된 템플릿이 박혀 있으면 요청 전에 멈춘다.

        초기 버전의 자동 템플릿화가 `is_timeline_search` 같은 불리언 플래그를
        검색어 자리로 오인해 `{keyword}` 를 넣는 결함이 있었다. 그대로 두면
        `is_timeline_search=로보락` 이 서버로 나간다. 조용히 이상한 요청을
        보내느니 여기서 멈추고 고치는 법을 알려주는 편이 낫다.
        """
        query = (self.cfg.endpoints.get("search") or {}).get("query") or {}
        bad = [
            k for k, v in query.items()
            if str(v) == "{keyword}"
            and k.lower().replace("-", "_").startswith(self._FLAG_PREFIXES)
        ]
        if not bad:
            return
        raise DataHubError(
            "검색 설정에 잘못된 값이 있습니다 — 기능 플래그에 검색어가 들어가 있습니다.\n"
            f"  문제 파라미터: {', '.join(bad)}  (각각 '{{keyword}}' 로 되어 있음)\n"
            "  이대로 요청하면 예) is_timeline_search=로보락 이 전송됩니다.\n"
            "  → 검색을 실행한 HAR로 이 명령을 돌리면 실제 값으로 복구됩니다:\n"
            "     python3 tools/repair_search_query.py --har page.har            # 미리보기\n"
            "     python3 tools/repair_search_query.py --har page.har --write    # 적용"
        )

    # ---------- 고수준: 검색 ----------
    def search(
        self,
        keyword: str,
        *,
        pages: int = 1,
        size: int = 50,
        start_datetime: str = "",
        end_datetime: str = "",
        use_cache: bool = True,
        extra_params: dict[str, Any] | None = None,
    ) -> list[BroadcastRef]:
        """키워드로 방송 목록을 찾는다.

        페이지를 넘기다가 빈 페이지가 나오거나, 새로 얻은 방송이 하나도 없으면
        멈춘다(같은 응답을 무한히 받는 설정 오류에 걸려 돌지 않도록).
        """
        if "search" not in self.cfg.endpoints:
            raise DataHubError(
                "설정에 'search' 엔드포인트가 없습니다. 검색 API 스펙이 아직 없습니다.\n"
                "  → 브라우저에서 실제로 검색을 한 번 한 뒤 그 HAR로\n"
                "     `hsbot devtools --har search.har --force` 를 돌리면 자동으로 추가됩니다."
            )
        self._check_search_query()
        collected: list[BroadcastRef] = []
        seen: set[tuple[str, str, str]] = set()
        for page in range(1, max(1, pages) + 1):
            params: dict[str, Any] = dict(
                keyword=keyword,
                query=keyword,
                page=page,
                # offset은 '쪽 번호'가 아니라 '건너뛸 개수'다. 둘을 같은 값으로 보내면
                # 2페이지를 요청해도 2건만 건너뛴 거의 같은 목록이 돌아온다.
                offset=(page - 1) * size,
                size=size,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
            params.update(extra_params or {})
            raw = self.get("search", use_cache=use_cache, **params)
            refs = self.to_refs(raw)
            fresh = [r for r in refs if r.dedup_key not in seen]
            if not fresh:
                break
            seen.update(r.dedup_key for r in fresh)
            collected.extend(fresh)
            if len(refs) < size:
                break
        return collected

    def to_refs(self, raw: Any) -> list[BroadcastRef]:
        """검색 응답 JSON → BroadcastRef 목록. 매핑은 전부 설정에서 읽는다."""
        sm = (self.cfg.mapping or {}).get("search") or {}
        list_paths = sm.get("list_paths", ["data.items", "data.list", "items", "results", "data"])
        items = first_found(raw, list_paths, _MISSING)
        if items is _MISSING:
            raise DataHubError(
                "검색 결과 목록 경로를 찾지 못했습니다. "
                "mapping.search.list_paths 를 실제 응답에 맞게 고치세요.\n"
                f"  시도한 경로: {list_paths}\n"
                f"  응답 최상위 키: {list(raw)[:10] if isinstance(raw, dict) else type(raw).__name__}"
            )
        if isinstance(items, dict):
            items = list(items.values())
        if not isinstance(items, list):
            raise DataHubError(
                f"검색 결과가 배열이 아닙니다(실제: {type(items).__name__}). "
                "mapping.search.list_paths 를 확인하세요."
            )

        key_paths = sm.get("product_key_paths", ["product_key", "productKey", "prd_key", "id"])
        name_paths = sm.get("product_name_paths", ["product_name", "name", "title", "goods_name"])
        ch_paths = sm.get("channel_paths", ["channel", "tv_channel", "shop_code"])
        chn_paths = sm.get("channel_name_paths", ["channel_name", "tv_channel_name", "shop_name"])
        st_paths = sm.get("start_paths", ["start_datetime", "start_time", "broadcast_start"])
        en_paths = sm.get("end_paths", ["end_datetime", "end_time", "broadcast_end"])
        pr_paths = sm.get("price_paths", ["sale_price", "price"])
        url_paths = sm.get("url_paths", ["url", "link", "detail_url"])

        out: list[BroadcastRef] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            pk = first_present(it, key_paths)
            url = first_present(it, url_paths)
            if not pk and url:
                pk = parse_datahub_url(str(url)).get("product_key")
            if not pk:
                continue
            pk = str(pk)
            channel = first_present(it, ch_paths) or (pk.split("_")[0] if "_" in pk else pk)
            price = first_present(it, pr_paths)
            out.append(
                BroadcastRef(
                    product_key=pk,
                    channel=str(channel),
                    channel_name=_as_str(first_present(it, chn_paths)),
                    product_name=str(first_present(it, name_paths, "") or ""),
                    tv_channel=_as_str(first_present(it, ch_paths)),
                    start_datetime=str(first_present(it, st_paths, "") or ""),
                    end_datetime=str(first_present(it, en_paths, "") or ""),
                    price=int(price) if isinstance(price, (int, float)) or str(price).isdigit() else None,
                    url=_as_str(url),
                )
            )
        return out

    def fetch_ref(self, ref: BroadcastRef, *, use_cache: bool = True) -> Broadcast:
        """검색으로 찾은 방송 1건의 자막을 가져온다."""
        bc = self.fetch_broadcast(
            product_key=ref.product_key,
            start_datetime=ref.start_datetime,
            end_datetime=ref.end_datetime or ref.start_datetime,
            tv_channel=ref.tv_channel,
            channel=ref.channel,
            product_name=ref.product_name or None,
            use_cache=use_cache,
        )
        if ref.channel_name:
            bc.channel_name = ref.channel_name
        if ref.price and not bc.price:
            bc.price = ref.price
        bc.extra["discovered_by"] = "search"
        return bc

    # ---------- 고수준: 방송 이력(최근 방송일자, 금액 제외) ----------
    def list_broadcasts(self, product_key: str, *, use_cache: bool = True) -> list[dict[str, Any]]:
        """상품 하나의 방송 이력을 최신순으로 반환한다.

        `/next-api/subtitle/products/{product_key}/broadcasts` 는 금액(실적) 없이
        `{broadcast_id, channel, start_datetime, end_datetime, duration_min}` 만 준다.
        실적은 별도 소스가 필요해 아직 여기서 다루지 않는다.
        """
        raw = self.get("broadcast_list", use_cache=use_cache, product_key=product_key)
        items = raw if isinstance(raw, list) else first_found(raw, ["data", "items", "results"], [])
        if not isinstance(items, list):
            items = []
        out = [dict(it) for it in items if isinstance(it, dict)]
        out.sort(key=lambda it: str(it.get("start_datetime") or ""), reverse=True)
        return out

    def fetch_by_broadcast_id(
        self,
        broadcast_id: Any,
        *,
        product_key: str,
        channel: str,
        start_datetime: str,
        end_datetime: str = "",
        product_name: str | None = None,
        use_cache: bool = True,
    ) -> Broadcast:
        """방송 이력에서 고른 broadcast_id 하나의 자막을 가져온다.

        실제 자막 엔드포인트는 product_key/시간대가 아니라 broadcast_id로만 찾는다.
        """
        sub_raw = self.get("subtitle", use_cache=use_cache, broadcast_id=broadcast_id)
        return self.to_broadcast(
            sub_raw,
            None,
            product_key=product_key,
            start_datetime=start_datetime,
            end_datetime=end_datetime or start_datetime,
            channel=channel,
            product_name=product_name,
        )

    # ---------- 고수준: 수집 ----------
    def fetch_broadcast(
        self,
        *,
        product_key: str,
        start_datetime: str,
        end_datetime: str,
        tv_channel: str | None = None,
        channel: str | None = None,
        product_name: str | None = None,
        use_cache: bool = True,
    ) -> Broadcast:
        """상품+시간대 하나를 정규화된 Broadcast로 가져온다."""
        params = dict(
            product_key=product_key,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            tv_channel=tv_channel or "",
        )
        meta_raw = (
            self.get("product", use_cache=use_cache, **params)
            if "product" in self.cfg.endpoints
            else {}
        )
        sub_raw = self.get("subtitle", use_cache=use_cache, **params)
        return self.to_broadcast(
            sub_raw,
            meta_raw,
            product_key=product_key,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            channel=channel or (tv_channel or product_key.split("_")[0]),
            product_name=product_name,
        )

    def to_broadcast(
        self,
        sub_raw: Any,
        meta_raw: Any = None,
        *,
        product_key: str,
        start_datetime: str,
        end_datetime: str,
        channel: str,
        product_name: str | None = None,
    ) -> Broadcast:
        """원본 JSON → Broadcast. 매핑은 전부 설정에서 읽는다."""
        mp = self.cfg.mapping
        seg_map = mp.get("segments", {})
        meta_map = mp.get("broadcast", {})

        origin = datetime.fromisoformat(start_datetime)
        if origin.tzinfo is None:
            origin = origin.replace(tzinfo=KST)

        list_paths = seg_map.get("list_paths", ["data", "items", "results"])
        items = first_found(sub_raw, list_paths, _MISSING)
        if items is _MISSING:
            # 경로 자체를 못 찾은 것과 '자막이 0줄'인 것은 다르다.
            # 전자는 매핑 설정 오류이므로 조용히 넘어가지 않고 알린다.
            raise DataHubError(
                "자막 목록 경로를 찾지 못했습니다. "
                "mapping.segments.list_paths 를 실제 응답에 맞게 고치세요.\n"
                f"  시도한 경로: {list_paths}\n"
                f"  응답 최상위 키: {list(sub_raw)[:10] if isinstance(sub_raw, dict) else type(sub_raw).__name__}"
            )
        if isinstance(items, dict):
            items = list(items.values())
        if not isinstance(items, list):
            raise DataHubError(
                f"자막 목록이 배열이 아닙니다(실제: {type(items).__name__}). "
                "mapping.segments.list_paths 를 확인하세요."
            )

        text_paths = seg_map.get("text_paths", ["text", "subtitle", "content"])
        start_paths = seg_map.get("start_paths", ["start_sec", "start_time", "startTime", "offset"])
        end_paths = seg_map.get("end_paths", ["end_sec", "end_time", "endTime"])
        speaker_paths = seg_map.get("speaker_paths", ["speaker", "host"])

        segments: list[Segment] = []
        for it in items:
            body = first_present(it, text_paths)
            if not body or not str(body).strip():
                continue
            raw_start = first_present(it, start_paths)
            try:
                start = to_seconds(raw_start, origin=origin) if raw_start is not None else 0.0
            except ValueError:
                start = 0.0
            raw_end = first_present(it, end_paths)
            try:
                end = to_seconds(raw_end, origin=origin) if raw_end is not None else None
            except ValueError:
                end = None
            segments.append(
                Segment(
                    start_sec=round(float(start), 3),
                    end_sec=end,
                    text=str(body).strip(),
                    speaker=first_present(it, speaker_paths),
                )
            )
        segments.sort(key=lambda s: s.start_sec)

        name = product_name or first_present(
            meta_raw or {}, meta_map.get("product_name_paths", ["data.name", "name", "title"]), product_key
        )
        price = first_present(meta_raw or {}, meta_map.get("price_paths", ["data.price", "price"]))
        ch_name = first_present(
            meta_raw or {}, meta_map.get("channel_name_paths", ["data.channel_name", "channel_name"])
        )

        return Broadcast(
            broadcast_id=f"{channel}:{product_key}:{start_datetime}",
            channel=channel,
            channel_name=ch_name,
            product_name=str(name),
            product_id=product_key,
            price=int(price) if isinstance(price, (int, float, str)) and str(price).isdigit() else None,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            segments=segments,
            source="datahub_api",
            extra={"n_raw_items": len(items)},
        )


def parse_datahub_url(url: str) -> dict[str, str]:
    """DataHub 웹 URL에서 수집 파라미터를 뽑아낸다.

    예) https://datahub.hsmoa.com/product/gsshop_1101476773
          ?tv_channel=gsmyshop&start_datetime=...&end_datetime=...&tab=subtitle
    """
    u = urllib.parse.urlparse(url)
    q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
    parts = [p for p in u.path.split("/") if p]
    product_key = parts[-1] if parts else ""
    out = {
        "product_key": product_key,
        "channel": product_key.split("_")[0] if "_" in product_key else product_key,
    }
    for k in ("tv_channel", "start_datetime", "end_datetime"):
        if k in q:
            out[k] = q[k]
    return out
