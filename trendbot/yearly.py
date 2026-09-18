"""추이를 연도별로 겹쳐보기 위한 피벗 — 월간(1~12월) / 주간(ISO 1~53주) 둘 다 지원.

예: 2024/2025/2026년의 같은 달(또는 같은 주)을 나란히 놓아 계절성·성장 여부를
한눈에 비교한다.
"""
from __future__ import annotations

from datetime import date

from .naver_api import TrendSeries


def yearly_overlay(series: TrendSeries) -> dict:
    """월간 TrendSeries → {"unit": "month", "periods": [1..12], "years": {...}}.

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
    periods = list(range(1, 13))
    return {
        "keyword": series.keyword,
        "unit": "month",
        "periods": periods,
        "years": {y: [by_year[y].get(m) for m in periods] for y in years},
    }


def weekly_overlay(series: TrendSeries) -> dict:
    """주간 TrendSeries → {"unit": "week", "periods": [1..53], "years": {...}}.

    주 번호는 ISO 8601 기준(isocalendar)이다. 연말/연초 경계의 주는 그 주가
    속한 ISO 연도로 묶인다(달력상의 1월 1일이 속한 연도와 다를 수 있음).
    """
    by_year: dict[str, dict[int, float]] = {}
    for p in series.points:
        try:
            d = date.fromisoformat(p.period[:10])
        except ValueError:
            continue
        iso_year, iso_week, _ = d.isocalendar()
        by_year.setdefault(str(iso_year), {})[iso_week] = p.ratio
    years = sorted(by_year)
    periods = list(range(1, 54))
    return {
        "keyword": series.keyword,
        "unit": "week",
        "periods": periods,
        "years": {y: [by_year[y].get(w) for w in periods] for y in years},
    }
