"""같은 상품을 판 여러 방송을 서로 비교한다."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .metrics import BroadcastMetrics
from .models import Broadcast
from . import textutil as tu


@dataclass
class AxisComparison:
    key: str
    label: str
    color: str
    values: dict[str, float]          # broadcast_id -> hits_per_min
    mean: float
    index: dict[str, float]           # 평균=100 기준 지수
    zscore: dict[str, float]
    leader: str                       # 가장 강하게 민 방송
    spread: float                     # max/mean. 채널 간 편차가 큰 축일수록 큼


@dataclass
class ComparisonResult:
    product_name: str
    metrics: list[BroadcastMetrics]
    axes: list[AxisComparison]
    distinctive: dict[str, list[tuple[str, float, int]]]   # bid -> [(term, z, count)]
    profile: dict[str, dict[str, Any]]                     # bid -> 요약 프로파일
    differentiating_axes: list[str] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        return [m.broadcast_id for m in self.metrics]

    def by_id(self, bid: str) -> BroadcastMetrics:
        return next(m for m in self.metrics if m.broadcast_id == bid)


def _stdev(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    mu = sum(xs) / len(xs)
    return math.sqrt(sum((x - mu) ** 2 for x in xs) / (len(xs) - 1))


def compare_axes(metrics: list[BroadcastMetrics]) -> list[AxisComparison]:
    keys: list[str] = []
    for m in metrics:
        for k in m.axes:
            if k not in keys:
                keys.append(k)

    out: list[AxisComparison] = []
    for k in keys:
        vals = {m.broadcast_id: m.axes[k].hits_per_min for m in metrics if k in m.axes}
        if not vals:
            continue
        xs = list(vals.values())
        mu = sum(xs) / len(xs)
        sd = _stdev(xs)
        any_axis = next(m.axes[k] for m in metrics if k in m.axes)
        out.append(
            AxisComparison(
                key=k,
                label=any_axis.label,
                color=any_axis.color,
                values=vals,
                mean=round(mu, 3),
                index={b: round(100 * v / mu, 1) if mu else 100.0 for b, v in vals.items()},
                zscore={b: round((v - mu) / sd, 2) if sd else 0.0 for b, v in vals.items()},
                leader=max(vals, key=lambda b: vals[b]),
                spread=round(max(xs) / mu, 2) if mu else 1.0,
            )
        )
    # 채널 간 차이가 큰 축을 앞에 둔다 = "여기서 갈렸다"
    out.sort(key=lambda a: -a.spread)
    return out


def distinctive_terms(
    broadcasts: list[Broadcast],
    *,
    top_n: int = 12,
    min_count: int = 3,
    alpha: float = 0.01,
) -> dict[str, list[tuple[str, float, int]]]:
    """어떤 방송이 '유독' 많이 쓴 표현을 찾는다.

    Monroe et al.(2008)의 Dirichlet 사전분포 로그오즈비(z-score)를 쓴다.
    단순 빈도 상위는 '이거/그거' 같은 공통어만 뽑히기 때문에,
    전체 코퍼스를 사전분포로 두고 '평균 대비 초과 사용'을 잰다.
    """
    per: dict[str, Counter] = {
        b.broadcast_id: tu.token_counts(b.full_text) for b in broadcasts
    }
    background: Counter = Counter()
    for c in per.values():
        background.update(c)
    a0 = alpha * sum(background.values())
    total_all = sum(background.values())

    result: dict[str, list[tuple[str, float, int]]] = {}
    for bid, counts in per.items():
        n_i = sum(counts.values())
        rest = Counter(background)
        rest.subtract(counts)
        n_r = total_all - n_i
        scored: list[tuple[str, float, int]] = []
        for term, y_i in counts.items():
            if y_i < min_count:
                continue
            a_w = alpha * background[term]
            y_r = max(rest[term], 0)
            num_i = y_i + a_w
            den_i = n_i + a0 - y_i - a_w
            num_r = y_r + a_w
            den_r = n_r + a0 - y_r - a_w
            if min(num_i, den_i, num_r, den_r) <= 0:
                continue
            delta = math.log(num_i / den_i) - math.log(num_r / den_r)
            var = 1.0 / num_i + 1.0 / num_r
            scored.append((term, round(delta / math.sqrt(var), 2), y_i))
        scored.sort(key=lambda t: -t[1])
        result[bid] = scored[:top_n]
    return result


def _fmt_min(sec: float | None) -> str | None:
    if sec is None:
        return None
    return f"{int(sec // 60)}분 {int(sec % 60)}초"


def build_profile(m: BroadcastMetrics, axes: list[AxisComparison]) -> dict[str, Any]:
    """한 방송의 판매 전략을 사람이 읽는 한 줄로 요약."""
    idx = {a.key: a.index.get(m.broadcast_id, 100.0) for a in axes}
    ranked = sorted(idx.items(), key=lambda kv: -kv[1])
    label = {a.key: a.label for a in axes}
    strong = [(label[k], v) for k, v in ranked[:3] if v >= 110]
    weak = [(label[k], v) for k, v in ranked[::-1][:3] if v <= 90]
    return {
        "channel": m.channel_name,
        "duration_min": m.duration_min,
        "chars_per_min": m.chars_per_min,
        "repetition_index": m.repetition_index,
        "strong_axes": strong,
        "weak_axes": weak,
        "first_price_at": _fmt_min(m.numeric.first_price_sec),
        "first_urgency_at": _fmt_min(
            m.axes["URGENCY"].first_hit_sec if "URGENCY" in m.axes else None
        ),
        "lowest_price": m.numeric.lowest_price,
        "max_percent": m.numeric.max_percent,
        "max_installment_months": m.numeric.max_installment_months,
    }


def compare(
    broadcasts: list[Broadcast],
    metrics: list[BroadcastMetrics],
    *,
    spread_threshold: float = 1.35,
) -> ComparisonResult:
    axes = compare_axes(metrics)
    return ComparisonResult(
        product_name=metrics[0].product_name if metrics else "",
        metrics=metrics,
        axes=axes,
        distinctive=distinctive_terms(broadcasts),
        profile={m.broadcast_id: build_profile(m, axes) for m in metrics},
        differentiating_axes=[a.key for a in axes if a.spread >= spread_threshold],
    )
