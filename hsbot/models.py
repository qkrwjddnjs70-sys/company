"""정규화된 데이터 모델.

홈쇼핑사/수집 경로가 달라도 이 스키마로 들어오면
metrics 이하 모든 계산이 동일하게 동작한다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

KST = timezone(timedelta(hours=9))


@dataclass
class Segment:
    """자막 한 줄 (발화 단위)."""

    start_sec: float           # 방송 시작 시점 기준 경과 초
    text: str
    end_sec: float | None = None
    speaker: str | None = None  # 쇼호스트/게스트 등 (제공되면)
    kinds: list[str] = field(default_factory=list)  # 소스가 자체 태깅한 발화 종류 (있으면)

    @property
    def duration_sec(self) -> float | None:
        if self.end_sec is None:
            return None
        return max(0.0, self.end_sec - self.start_sec)

    @property
    def n_chars(self) -> int:
        """공백 제외 글자 수. 한국어 발화량 대리지표."""
        return len("".join(self.text.split()))


@dataclass
class Broadcast:
    """방송 1회분."""

    broadcast_id: str
    channel: str                  # 내부 코드 (gsshop, cjonstyle, ...)
    product_name: str
    start_datetime: str           # ISO8601
    end_datetime: str
    segments: list[Segment] = field(default_factory=list)
    channel_name: str | None = None
    product_id: str | None = None
    price: int | None = None
    source: str = "unknown"       # datahub_api / paste / local_json
    extra: dict[str, Any] = field(default_factory=dict)

    # ---------- 파생값 ----------
    @property
    def display_channel(self) -> str:
        return self.channel_name or self.channel

    @property
    def duration_sec(self) -> float:
        """편성 길이. 메타데이터 우선, 없으면 자막 마지막 시각으로 대체."""
        try:
            s = datetime.fromisoformat(self.start_datetime)
            e = datetime.fromisoformat(self.end_datetime)
            meta = (e - s).total_seconds()
            if meta > 0:
                return meta
        except (ValueError, TypeError):
            pass
        if self.segments:
            last = self.segments[-1]
            return float(last.end_sec or last.start_sec) or 1.0
        return 1.0

    @property
    def duration_min(self) -> float:
        return self.duration_sec / 60.0

    @property
    def full_text(self) -> str:
        return "\n".join(s.text for s in self.segments)

    def sorted_segments(self) -> list[Segment]:
        return sorted(self.segments, key=lambda s: s.start_sec)

    # ---------- 직렬화 ----------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["duration_sec"] = self.duration_sec
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Broadcast":
        d = dict(d)
        d.pop("duration_sec", None)
        segs = [Segment(**s) if isinstance(s, dict) else s for s in d.pop("segments", [])]
        known = {f for f in cls.__dataclass_fields__ if f != "segments"}
        extra = d.pop("extra", {}) or {}
        for k in list(d):
            if k not in known:
                extra[k] = d.pop(k)
        return cls(segments=segs, extra=extra, **d)


def save_broadcasts(path: str, broadcasts: Iterable[Broadcast]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([b.to_dict() for b in broadcasts], f, ensure_ascii=False, indent=2)


def load_broadcasts(path: str) -> list[Broadcast]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        raw = raw.get("broadcasts", [raw])
    return [Broadcast.from_dict(x) for x in raw]
