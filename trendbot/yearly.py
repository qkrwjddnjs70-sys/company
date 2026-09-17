"""월간 추이를 연도별로 겹쳐보기 위한 피벗 (예: 2024/2025/2026 1~12월 비교)."""
from __future__ import annotations

from .naver_api import TrendSeries


def yearly_overlay(series: TrendSeries) -> dict:
    """월간 TrendSeries → {"months": [1..12], "years": {"2024": [...12개], ...}}.

    데이터가 없는 달(예: 3년 전 아직 시작 전인 달)은 None으로 채워 차트가
    선을 억지로 잇지 않게 한다.
    """
    by_year: dict[str, dict[int, float]] = {}
    for p in series.points:
        if len(p.period) < 7:
            continue
        year, month = p.period[:4], int(p.period[5:7])
        by_year.setdefault(year, {})[month] = p.ratio
    years = sorted(by_year)
    return {
        "keyword": series.keyword,
        "months": list(range(1, 13)),
        "years": {y: [by_year[y].get(m) for m in range(1, 13)] for y in years},
    }
