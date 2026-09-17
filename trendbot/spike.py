"""급상승(급등) 키워드 탐지.

최근 N주 평균 검색 비율을 그 이전 M주 평균과 비교해 증가율을 매긴다.
과거 관측치가 0(또는 config의 min_ratio_floor 미만)이었다가 갑자기 값이 생긴
경우는 '신규 급증'으로 따로 표시한다(0으로 나눌 수 없어 증가율이 정의되지 않음).
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SpikeResult:
    keyword: str
    baseline_avg: float
    recent_avg: float
    growth_pct: float | None  # None이면 is_new=True (증가율 정의 불가)
    is_new: bool
    labels: list[str] = field(default_factory=list)

    @property
    def sort_key(self) -> float:
        # 신규 급증을 항상 최상단에 둔다 — 후보군에 아예 없던 수요라 가장 주목할 만하다.
        return float("inf") if self.is_new else (self.growth_pct or 0.0)


def compute_spike(
    ratios: list[float], *, recent_weeks: int, baseline_weeks: int
) -> tuple[float, float, float | None, bool] | None:
    """(baseline_avg, recent_avg, growth_pct, is_new)를 반환한다.

    관측치가 recent_weeks + baseline_weeks 보다 적으면 판단할 수 없으므로 None.
    """
    need = recent_weeks + baseline_weeks
    if len(ratios) < need:
        return None
    recent = ratios[-recent_weeks:]
    baseline = ratios[-need:-recent_weeks]
    recent_avg = sum(recent) / len(recent)
    baseline_avg = sum(baseline) / len(baseline)
    if baseline_avg <= 0:
        return baseline_avg, recent_avg, None, recent_avg > 0
    growth_pct = (recent_avg - baseline_avg) / baseline_avg * 100.0
    return baseline_avg, recent_avg, growth_pct, False


def rank_spikes(
    keyword_ratios: dict[str, list[float]],
    *,
    recent_weeks: int = 2,
    baseline_weeks: int = 8,
    min_growth_pct: float = 30.0,
    min_ratio_floor: float = 1.0,
    keyword_labels: dict[str, list[str]] | None = None,
) -> list[SpikeResult]:
    """증가율 기준 내림차순 정렬된 급상승 키워드 목록.

    min_ratio_floor로 절대 검색량이 너무 작아 비율 잡음(0.1 → 0.3처럼 %는 크지만
    무의미한 변화)인 항목을 걸러낸다.
    """
    keyword_labels = keyword_labels or {}
    out: list[SpikeResult] = []
    for kw, ratios in keyword_ratios.items():
        computed = compute_spike(ratios, recent_weeks=recent_weeks, baseline_weeks=baseline_weeks)
        if computed is None:
            continue
        baseline_avg, recent_avg, growth_pct, is_new = computed
        if recent_avg < min_ratio_floor:
            continue
        if not is_new and (growth_pct is None or growth_pct < min_growth_pct):
            continue
        out.append(SpikeResult(
            keyword=kw, baseline_avg=baseline_avg, recent_avg=recent_avg,
            growth_pct=growth_pct, is_new=is_new, labels=keyword_labels.get(kw, []),
        ))
    out.sort(key=lambda r: r.sort_key, reverse=True)
    return out
