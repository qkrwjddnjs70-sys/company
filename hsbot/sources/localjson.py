"""이미 정규화해 저장해 둔 JSON을 다시 읽는다 (캐시/재분석용)."""
from __future__ import annotations

from ..models import Broadcast, load_broadcasts


def load_local(path: str) -> list[Broadcast]:
    return load_broadcasts(path)


def load_one(path: str) -> Broadcast:
    bcs = load_broadcasts(path)
    if len(bcs) != 1:
        raise ValueError(f"방송 1건이 필요한데 {len(bcs)}건이 들어 있습니다: {path}")
    return bcs[0]
