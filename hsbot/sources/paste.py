"""붙여넣기 텍스트 파서.

DataHub UI의 자막 탭을 그대로 복사해 붙여넣은 텍스트를 Broadcast로 만든다.
API 연동 전 검증용이자, API에 자막이 없을 때의 상시 대안이다.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Iterable

from ..models import Broadcast, Segment, KST
from .base import to_seconds

# 줄 앞머리의 시각 표기를 잡아낸다. (시작 [- 종료]) + 본문
_LINE_RE = re.compile(
    r"^\s*[\[\(]?\s*"
    r"(?P<start>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2})?(?:[+\-]\d{2}:?\d{2})?"
    r"|(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?)"
    r"\s*(?:[-~–]{1,2}\s*(?P<end>(?:\d+:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?))?"
    r"\s*[\]\)]?\s*[:\|\t]?\s*(?P<text>\S.*)$"
)
_SPEAKER_RE = re.compile(r"^\s*(?P<speaker>[가-힣A-Za-z][가-힣A-Za-z0-9 ]{0,12})\s*[:：]\s*(?P<rest>\S.*)$")


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace(" ", "T").replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=KST)


def parse_subtitle_text(
    text: str,
    *,
    broadcast_id: str,
    channel: str,
    product_name: str,
    start_datetime: str,
    end_datetime: str | None = None,
    channel_name: str | None = None,
    product_id: str | None = None,
    price: int | None = None,
    split_speaker: bool = True,
) -> Broadcast:
    """자막 텍스트 → Broadcast.

    타임스탬프가 없는 줄은 방송 길이에 균등 배분한다(구성 분석 정확도는 떨어짐).
    """
    origin = _parse_dt(start_datetime)
    raw_lines = [ln for ln in text.splitlines() if ln.strip()]

    parsed: list[tuple[float | None, float | None, str]] = []
    for ln in raw_lines:
        m = _LINE_RE.match(ln)
        if m:
            start = to_seconds(m.group("start"), origin=origin)
            end = to_seconds(m.group("end"), origin=origin) if m.group("end") else None
            parsed.append((start, end, m.group("text").strip()))
        else:
            parsed.append((None, None, ln.strip()))

    known = [p for p in parsed if p[0] is not None]
    if known:
        total = max(p[0] for p in known)
    else:
        total = 0.0
    if end_datetime:
        total = max(total, (_parse_dt(end_datetime) - origin).total_seconds())
    if total <= 0:
        total = float(len(parsed))  # 최후의 보루: 줄당 1초

    segments: list[Segment] = []
    last_known = 0.0
    n = len(parsed)
    for i, (start, end, body) in enumerate(parsed):
        if start is None:
            start = last_known if known else total * i / max(n - 1, 1)
        else:
            last_known = start
        speaker = None
        if split_speaker:
            sm = _SPEAKER_RE.match(body)
            if sm and len(sm.group("speaker")) <= 12:
                speaker, body = sm.group("speaker"), sm.group("rest")
        if body:
            segments.append(Segment(start_sec=round(start, 3), end_sec=end, text=body, speaker=speaker))

    if not end_datetime:
        end_datetime = (origin + timedelta(seconds=total)).isoformat()

    return Broadcast(
        broadcast_id=broadcast_id,
        channel=channel,
        channel_name=channel_name,
        product_name=product_name,
        product_id=product_id,
        price=price,
        start_datetime=origin.isoformat(),
        end_datetime=end_datetime,
        segments=segments,
        source="paste",
    )


def load_paste_file(path: str, **kwargs) -> Broadcast:
    with open(path, encoding="utf-8") as f:
        body = f.read()
    # '#'으로 시작하는 머리말은 메모로 간주하고 제외
    body = "\n".join(ln for ln in body.splitlines() if not ln.lstrip().startswith("#"))
    return parse_subtitle_text(body, **kwargs)


def load_many(specs: Iterable[dict]) -> list[Broadcast]:
    """[{path: ..., broadcast_id: ..., ...}, ...] 형태의 배치 로딩."""
    out = []
    for spec in specs:
        spec = dict(spec)
        out.append(load_paste_file(spec.pop("path"), **spec))
    return out
