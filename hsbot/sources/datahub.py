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

from ..models import Broadcast, Segment, KST
from .base import dig, first_found, first_present, to_seconds

DEFAULT_CONFIG_PATH = os.path.join("config", "datahub.json")

_MISSING = object()   # "경로 없음"과 "빈 배열"을 구분하기 위한 센티넬


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
                f"  → {example} 를 복사해 실제 API 스펙에 맞게 고친 뒤 다시 실행하세요."
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
        self.api_key = api_key or os.environ.get(self.cfg.api_key_env, "")
        self._last_call = 0.0
        if self.cfg.cache_dir:
            os.makedirs(self.cfg.cache_dir, exist_ok=True)

    # ---------- 저수준 ----------
    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json", "User-Agent": "hsbot/0.1"}
        auth = self.cfg.auth or {}
        if auth.get("type") == "header":
            if not self.api_key:
                raise DataHubError(
                    f"API 키가 없습니다. 환경변수 {self.cfg.api_key_env} 를 설정하세요."
                )
            h[auth.get("header", "Authorization")] = auth.get(
                "value_template", "Bearer {api_key}"
            ).format(api_key=self.api_key)
        h.update(auth.get("extra_headers", {}) or {})
        return h

    def _url(self, name: str, params: dict[str, Any]) -> str:
        ep = self.cfg.endpoints.get(name)
        if not ep:
            raise DataHubError(f"설정에 '{name}' 엔드포인트가 없습니다.")
        path = ep["path"].format(**params)
        query = {}
        for k, tmpl in (ep.get("query") or {}).items():
            v = str(tmpl).format(**params)
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
                req = urllib.request.Request(url, headers=self._headers())
                with urllib.request.urlopen(req, timeout=self.cfg.timeout_sec) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self._last_call = time.monotonic()
                if cp:
                    with open(cp, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                return data
            except urllib.error.HTTPError as e:
                # 4xx는 재시도해도 소용없다 (429 제외)
                if e.code != 429 and 400 <= e.code < 500:
                    raise DataHubError(f"HTTP {e.code} {e.reason} — {url}") from e
                last_err = e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last_err = e
            if attempt < self.cfg.max_retries - 1:
                time.sleep(delay)
                delay *= 2
        raise DataHubError(f"요청 실패({self.cfg.max_retries}회 재시도): {url}\n  마지막 오류: {last_err}")

    # ---------- 고수준 ----------
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
