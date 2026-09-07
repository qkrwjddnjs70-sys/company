"""수집 어댑터 공통 유틸: 시각 파싱과 점 표기 경로 조회."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

_HMS_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?$")


def to_seconds(value: Any, *, origin: datetime | None = None) -> float:
    """자막 시각 표기를 '방송 시작 후 경과 초'로 통일한다.

    지원 형식
      - 숫자 (초 또는 밀리초로 해석)
      - "00:05:12", "5:12", "00:05:12.340"
      - ISO8601 절대시각 (origin이 주어지면 그 차이를 계산)
    """
    if value is None:
        raise ValueError("시각 값이 비어 있습니다.")
    if isinstance(value, (int, float)):
        v = float(value)
        return v / 1000.0 if v > 100_000 else v   # 10만 초(≈27시간) 초과면 ms로 간주
    s = str(value).strip()
    m = _HMS_RE.match(s)
    if m:
        h, mm, ss, ms = m.groups()
        return int(h or 0) * 3600 + int(mm) * 60 + int(ss) + int((ms or "0").ljust(3, "0")) / 1000
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"시각 형식을 해석할 수 없습니다: {value!r}") from exc
    if origin is None:
        raise ValueError("절대시각을 쓰려면 방송 시작시각(origin)이 필요합니다.")
    if (dt.tzinfo is None) != (origin.tzinfo is None):
        dt = dt.replace(tzinfo=origin.tzinfo) if dt.tzinfo is None else dt
        origin = origin.replace(tzinfo=dt.tzinfo) if origin.tzinfo is None else origin
    return (dt - origin).total_seconds()


def dig(obj: Any, path: str | None, default: Any = None) -> Any:
    """'data.items.0.text' 같은 점 표기 경로로 중첩 구조를 조회한다.

    빈 문자열과 '(root)'는 루트 자신을 가리킨다(응답 최상위가 배열인 경우).
    """
    if path in ("", "(root)"):
        return obj
    if not path:
        return default
    cur = obj
    for part in path.split("."):
        if cur is None:
            return default
        if isinstance(cur, list):
            try:
                cur = cur[int(part)]
                continue
            except (ValueError, IndexError):
                return default
        if isinstance(cur, dict):
            if part not in cur:
                return default
            cur = cur[part]
        else:
            cur = getattr(cur, part, None)
    return default if cur is None else cur


def first_present(obj: Any, paths: list[str], default: Any = None) -> Any:
    """후보 경로를 순서대로 시도한다. 응답 스키마가 확정되지 않았을 때 유용."""
    for p in paths:
        v = dig(obj, p)
        if v not in (None, "", []):
            return v
    return default


_ABSENT = object()


def first_found(obj: Any, paths: list[str], missing: Any = None) -> Any:
    """first_present와 달리 빈 리스트/0도 '찾은 값'으로 인정한다.

    응답에 자막이 0줄인 것과, 매핑 경로가 틀린 것을 구분해야 할 때 쓴다.
    """
    for p in paths:
        v = dig(obj, p, _ABSENT)
        if v is not _ABSENT:
            return v
    return missing
