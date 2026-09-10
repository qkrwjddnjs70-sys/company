"""방송 1회분을 숫자로 환산하는 엔진.

설계 원칙
---------
1. 방송 길이가 다르면 총량 비교는 무의미하다 → 모든 지표는 '분당(per-minute)'으로 정규화한다.
2. 축(axis)은 상호배타적이지 않다. 한 문장이 가격+긴급을 동시에 자극할 수 있고,
   그게 실제 판매 화법이므로 중복 카운트를 허용한다.
3. '얼마나 말했나'(강도)와 '언제 말했나'(구성)를 분리해서 잰다.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .lexicon import Lexicon
from .models import Broadcast
from . import textutil as tu

DEFAULT_BUCKET_SEC = 300  # 타임라인 기본 5분 단위


@dataclass
class AxisMetric:
    key: str
    label: str
    color: str
    hits: int = 0                  # 키워드 매칭 총 횟수
    hits_per_min: float = 0.0      # 분당 매칭 (강도)
    segment_coverage: float = 0.0  # 이 축을 건드린 자막 줄의 비율 (0~1)
    first_hit_sec: float | None = None   # 처음 꺼낸 시점
    peak_bucket_idx: int | None = None   # 가장 집중된 구간
    peak_bucket_hits: int = 0
    top_terms: list[tuple[str, int]] = field(default_factory=list)
    example_lines: list[dict[str, Any]] = field(default_factory=list)  # {"t": sec, "text": ..., "terms": [...]}


@dataclass
class NumericMetric:
    """숫자 소구(가격/할인율/할부) 분석."""

    price_mentions: int = 0
    distinct_prices: list[int] = field(default_factory=list)
    lowest_price: int | None = None
    highest_price: int | None = None
    price_mentions_per_min: float = 0.0
    first_price_sec: float | None = None
    percent_mentions: int = 0
    max_percent: float | None = None
    installment_mentions: int = 0
    max_installment_months: int | None = None


@dataclass
class BroadcastMetrics:
    broadcast_id: str
    channel: str
    channel_name: str
    product_name: str
    start_datetime: str
    duration_min: float
    n_segments: int
    n_chars: int
    chars_per_min: float
    segments_per_min: float
    avg_chars_per_segment: float
    type_token_ratio: float
    repetition_index: float
    axes: dict[str, AxisMetric]
    numeric: NumericMetric
    bucket_sec: int
    timeline: list[dict[str, Any]]
    top_keywords: list[tuple[str, int]]

    def axis_vector(self, keys: list[str] | None = None) -> dict[str, float]:
        """채널 간 비교에 쓰는 분당 강도 벡터."""
        keys = keys or list(self.axes)
        return {k: self.axes[k].hits_per_min for k in keys if k in self.axes}

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["axes"] = {k: asdict(v) for k, v in self.axes.items()}
        d["numeric"] = asdict(self.numeric)
        return d


def _bucket_count(duration_sec: float, bucket_sec: int) -> int:
    return max(1, int((duration_sec + bucket_sec - 1) // bucket_sec))


def analyze(
    bc: Broadcast,
    lex: Lexicon,
    *,
    bucket_sec: int = DEFAULT_BUCKET_SEC,
    top_n: int = 25,
) -> BroadcastMetrics:
    segs = bc.sorted_segments()
    dur_min = max(bc.duration_min, 1e-9)
    n_buckets = _bucket_count(bc.duration_sec, bucket_sec)

    # ---------- 발화량 ----------
    n_chars = sum(s.n_chars for s in segs)
    counts = tu.token_counts(bc.full_text, use_bigrams=False)
    total_tok = sum(counts.values())
    ttr = (len(counts) / total_tok) if total_tok else 0.0
    # 반복지수: 상위 20개 토큰이 전체에서 차지하는 비중. 높을수록 같은 말을 반복.
    rep = (sum(c for _, c in counts.most_common(20)) / total_tok) if total_tok else 0.0

    # ---------- 축별 집계 ----------
    axis_metrics: dict[str, AxisMetric] = {}
    axis_bucket_hits: dict[str, list[int]] = {}
    for ax in lex:
        hits_total = 0
        seg_with_hit = 0
        first_sec: float | None = None
        buckets = [0] * n_buckets
        term_counts: dict[str, int] = {}
        examples: list[dict[str, Any]] = []
        for s in segs:
            h = ax.hits(s.text)
            if not h:
                continue
            hits_total += len(h)
            seg_with_hit += 1
            if first_sec is None:
                first_sec = s.start_sec
            bi = min(int(s.start_sec // bucket_sec), n_buckets - 1)
            buckets[bi] += len(h)
            for term in h:
                term_counts[term] = term_counts.get(term, 0) + 1
            if len(examples) < 30:
                examples.append({"t": s.start_sec, "text": s.text, "terms": sorted(set(h))})
        peak_idx = max(range(n_buckets), key=lambda i: buckets[i]) if hits_total else None
        axis_metrics[ax.key] = AxisMetric(
            key=ax.key,
            label=ax.label,
            color=ax.color,
            hits=hits_total,
            hits_per_min=round(hits_total / dur_min, 3),
            segment_coverage=round(seg_with_hit / len(segs), 4) if segs else 0.0,
            first_hit_sec=first_sec,
            peak_bucket_idx=peak_idx,
            peak_bucket_hits=buckets[peak_idx] if peak_idx is not None else 0,
            top_terms=sorted(term_counts.items(), key=lambda kv: -kv[1])[:8],
            example_lines=examples,
        )
        axis_bucket_hits[ax.key] = buckets

    # ---------- 숫자 소구 ----------
    num = NumericMetric()
    prices: list[int] = []
    for s in segs:
        p = tu.extract_prices(s.text)
        if p:
            prices.extend(p)
            num.price_mentions += len(p)
            if num.first_price_sec is None:
                num.first_price_sec = s.start_sec
        pct = tu.extract_percents(s.text)
        if pct:
            num.percent_mentions += len(pct)
            num.max_percent = max(num.max_percent or 0.0, max(pct))
        mon = tu.extract_months(s.text)
        if mon:
            num.installment_mentions += len(mon)
            num.max_installment_months = max(num.max_installment_months or 0, max(mon))
    if prices:
        num.distinct_prices = sorted(set(prices))
        num.lowest_price = min(prices)
        num.highest_price = max(prices)
    num.price_mentions_per_min = round(num.price_mentions / dur_min, 3)

    # ---------- 타임라인 ----------
    bucket_chars = [0] * n_buckets
    bucket_segs = [0] * n_buckets
    for s in segs:
        bi = min(int(s.start_sec // bucket_sec), n_buckets - 1)
        bucket_chars[bi] += s.n_chars
        bucket_segs[bi] += 1
    bmin = bucket_sec / 60.0
    timeline = [
        {
            "idx": i,
            "start_sec": i * bucket_sec,
            "label": f"{i * bucket_sec // 60}~{min((i + 1) * bucket_sec // 60, int(bc.duration_sec // 60))}분",
            "chars_per_min": round(bucket_chars[i] / bmin, 1),
            "segments": bucket_segs[i],
            "axes": {k: round(v[i] / bmin, 3) for k, v in axis_bucket_hits.items()},
        }
        for i in range(n_buckets)
    ]

    return BroadcastMetrics(
        broadcast_id=bc.broadcast_id,
        channel=bc.channel,
        channel_name=bc.display_channel,
        product_name=bc.product_name,
        start_datetime=bc.start_datetime,
        duration_min=round(dur_min, 2),
        n_segments=len(segs),
        n_chars=n_chars,
        chars_per_min=round(n_chars / dur_min, 1),
        segments_per_min=round(len(segs) / dur_min, 2),
        avg_chars_per_segment=round(n_chars / len(segs), 1) if segs else 0.0,
        type_token_ratio=round(ttr, 4),
        repetition_index=round(rep, 4),
        axes=axis_metrics,
        numeric=num,
        bucket_sec=bucket_sec,
        timeline=timeline,
        top_keywords=tu.token_counts(bc.full_text).most_common(top_n),
    )
