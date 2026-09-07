"""검색으로 찾은 방송 목록을 다루는 계층.

`hsbot search "로보락"` 은 두 단계로 나뉜다.

    (1) 탐색  — 키워드로 방송 목록(BroadcastRef)을 받는다     ← 네트워크 필요
    (2) 선별  — 비교에 쓸 방송만 걸러낸다                      ← 순수 함수, 여기 모듈

(2)를 (1)에서 분리한 이유는 두 가지다.
  · 네트워크 없이 테스트할 수 있다.
  · HAR에서 뽑은 목록에도 똑같은 선별 규칙을 적용할 수 있다.

**왜 그냥 다 모으면 안 되는가.** "로보락"으로 검색하면 S9 MAX Ultra, Q Revo,
심지어 로보락 아닌 상품까지 섞여 나온다. 서로 다른 모델을 한 표에 올리면
"채널별 화법 차이"가 아니라 "제품 차이"를 재게 된다. 그래서 모델 단위로
묶고(group_by_model), 2개 채널 이상 겹치는 모델만 비교 대상으로 삼는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Any, Iterable

from .models import KST

# 모델 코드로 볼 수 없는, 마케팅/카테고리 단어. 모델 키 계산에서 제외한다.
_MODEL_STOPWORDS = {
    "로봇청소기", "청소기", "무선청소기", "정품", "새상품", "단독", "특가", "최저가",
    "본사", "직영", "무료배송", "사은품", "세트", "패키지", "구성", "혜택", "행사",
    "신제품", "출시", "한정", "선착순", "앵콜", "앙콜", "재입고", "실속", "프리미엄",
    "KC", "AS", "TV", "HD", "NEW", "SET", "GIFT", "EVENT",
}
# 숫자가 없어도 모델명의 일부로 인정하는 접미 토큰 (S9 MAX Ultra 의 MAX/Ultra)
_MODEL_SUFFIX = {"MAX", "ULTRA", "PRO", "PLUS", "LITE", "MINI", "AI", "OMNI", "EDGE", "SE"}

_BRACKET = re.compile(r"[\[\(【（][^\]\)】）]*[\]\)】）]")
# 한 글자 영문도 토큰으로 남긴다 — "Q Revo Pro" 의 Q 가 곧 모델 코드다.
_TOKEN = re.compile(r"[A-Za-z]+\d+[A-Za-z0-9]*|[A-Za-z]+|\d+[A-Za-z]+|[가-힣]+")


@dataclass
class BroadcastRef:
    """수집 후보 방송 1건. 아직 자막은 없고 '어디서 무엇을 언제 팔았는지'만 있다."""

    product_key: str
    channel: str
    start_datetime: str
    end_datetime: str = ""
    channel_name: str | None = None
    product_name: str = ""
    tv_channel: str | None = None
    price: int | None = None
    url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    # ---------- 파생값 ----------
    @property
    def display_channel(self) -> str:
        return self.channel_name or self.channel

    @property
    def start_dt(self) -> datetime | None:
        return _parse_dt(self.start_datetime)

    @property
    def end_dt(self) -> datetime | None:
        return _parse_dt(self.end_datetime)

    @property
    def duration_min(self) -> float | None:
        s, e = self.start_dt, self.end_dt
        if not s or not e:
            return None
        return max(0.0, (e - s).total_seconds()) / 60.0

    @property
    def model_key(self) -> str:
        """같은 제품끼리 묶기 위한 키. `model_key("로보락 S9 MAX Ultra") == "S9 MAX ULTRA"`"""
        return model_key(self.product_name)

    @property
    def dedup_key(self) -> tuple[str, str, str]:
        """같은 방송이 중복으로 잡혔는지 판단하는 키 (채널+상품+시작시각)."""
        return (self.channel, self.product_key, self.start_datetime[:16])

    @property
    def fetchable(self) -> bool:
        """자막을 실제로 요청할 수 있는가. 시작시각이 없으면 수집 자체가 불가능하다."""
        return bool(self.product_key and self.start_datetime)

    # ---------- 직렬화 ----------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "BroadcastRef":
        d = dict(d)
        known = set(cls.__dataclass_fields__)
        extra = d.pop("extra", {}) or {}
        for k in list(d):
            if k not in known:
                extra[k] = d.pop(k)
        d.setdefault("product_key", "")
        d.setdefault("channel", "")
        d.setdefault("start_datetime", "")
        return cls(extra=extra, **d)


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=KST)


def _starts_model(token: str) -> bool:
    """모델명을 시작할 수 있는 토큰인가.

    `S9`(영문+숫자)와 `Q`(짧은 대문자 코드)는 모델의 시작이 될 수 있다.
    반면 `AI`·`PRO` 같은 수식 접미어는 단독으로 모델을 시작하지 못한다
    ("제트봇 AI"의 AI를 모델 코드로 착각하는 것을 막는다).
    """
    up = token.upper()
    if up in _MODEL_SUFFIX:
        return False
    if any(c.isdigit() for c in token) and any(c.isalpha() for c in token):
        return True
    return len(token) <= 2 and token.isascii() and token.isalpha() and token.isupper()


def model_key(product_name: str) -> str:
    """상품명에서 모델 식별자만 남긴다.

        "[단독] 로보락 S9 MAX Ultra 로봇청소기 정품" → "S9 MAX ULTRA"
        "로보락 Q Revo Pro 물걸레"                  → "Q REVO PRO"

    규칙: 모델을 시작할 수 있는 토큰(`_starts_model`)을 만나면 거기서부터
    끝까지의 비-불용어 토큰을 모델명으로 본다. 모델 토큰을 못 찾으면
    브랜드를 포함한 앞쪽 토큰 3개로 대체하므로, 최소한 서로 다른 상품이
    한 그룹으로 섞이지는 않는다.

    **근사임을 전제로 쓸 것.** 채널마다 상품명 표기가 크게 다르면 같은 제품이
    다른 키로 갈릴 수 있다. `hsbot search` 출력의 모델별 묶음을 눈으로 확인하고,
    어긋나면 `--model` / `--require` 로 직접 지정하는 편이 확실하다.
    """
    if not product_name:
        return ""
    cleaned = _BRACKET.sub(" ", product_name)
    tokens = [t for t in _TOKEN.findall(cleaned) if t.upper() not in _MODEL_STOPWORDS]

    start = next((i for i, t in enumerate(tokens) if _starts_model(t)), None)
    if start is not None:
        picked = [
            t.upper() for t in tokens[start:]
            if t.isascii() and (any(c.isdigit() for c in t) or t.isalpha())
        ]
        if picked:
            return " ".join(picked)
    return " ".join(t.upper() for t in tokens[:3])


def dedupe(refs: Iterable[BroadcastRef]) -> list[BroadcastRef]:
    """같은 방송이 여러 번 잡힌 것을 하나로 합친다 (먼저 온 것을 남긴다)."""
    seen: set[tuple[str, str, str]] = set()
    out: list[BroadcastRef] = []
    for r in refs:
        if r.dedup_key in seen:
            continue
        seen.add(r.dedup_key)
        out.append(r)
    return out


def filter_refs(
    refs: Iterable[BroadcastRef],
    *,
    require: Iterable[str] = (),
    exclude: Iterable[str] = (),
    channels: Iterable[str] = (),
    since: str | None = None,
    until: str | None = None,
    min_minutes: float | None = None,
    limit: int | None = None,
) -> list[BroadcastRef]:
    """비교 대상 후보를 좁힌다. 조건은 모두 AND로 결합된다.

    require/exclude 는 상품명 부분일치(대소문자 무시).
    channels 는 채널 코드 또는 채널명 부분일치.
    since/until 은 방송 시작시각 기준 (ISO8601, 날짜만 줘도 됨).
    """
    req = [s.lower() for s in require if s]
    exc = [s.lower() for s in exclude if s]
    chs = [s.lower() for s in channels if s]
    lo, hi = _parse_dt(since), _parse_dt(until)

    out: list[BroadcastRef] = []
    for r in refs:
        name = (r.product_name or "").lower()
        if req and not all(k in name for k in req):
            continue
        if exc and any(k in name for k in exc):
            continue
        if chs:
            hay = f"{r.channel} {r.channel_name or ''}".lower()
            if not any(c in hay for c in chs):
                continue
        st = r.start_dt
        if lo and (st is None or st < lo):
            continue
        if hi and (st is None or st > hi):
            continue
        if min_minutes is not None:
            d = r.duration_min
            if d is not None and d < min_minutes:
                continue
        out.append(r)

    out.sort(key=lambda r: (r.start_datetime or "", r.channel))
    return out[:limit] if limit else out


def group_by_model(refs: Iterable[BroadcastRef]) -> dict[str, list[BroadcastRef]]:
    """모델 단위로 묶는다. 방송 수가 많은 모델이 앞에 온다."""
    groups: dict[str, list[BroadcastRef]] = {}
    for r in refs:
        groups.setdefault(r.model_key or "(미상)", []).append(r)
    for v in groups.values():
        v.sort(key=lambda r: (r.start_datetime or "", r.channel))
    return dict(sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])))


def comparable_groups(
    refs: Iterable[BroadcastRef], *, min_channels: int = 2
) -> list[tuple[str, list[BroadcastRef]]]:
    """비교가 성립하는 모델 그룹만 고른다.

    한 채널에서만 판 모델은 '채널 간 비교'가 불가능하므로 제외한다.
    같은 채널이 같은 모델을 여러 번 방송했다면 그건 남긴다(재방송 비교는 유의미).
    """
    out = []
    for model, group in group_by_model(refs).items():
        if len({r.channel for r in group}) >= min_channels:
            out.append((model, group))
    return out


def pick_best_group(
    refs: Iterable[BroadcastRef], *, min_channels: int = 2
) -> tuple[str, list[BroadcastRef]] | None:
    """자동 수집에서 기본으로 쓸 그룹: 겹치는 채널이 가장 많은 모델."""
    groups = comparable_groups(refs, min_channels=min_channels)
    if not groups:
        return None
    return max(groups, key=lambda kv: (len({r.channel for r in kv[1]}), len(kv[1])))


def to_targets_spec(
    refs: Iterable[BroadcastRef], *, product_name: str = "", lexicons: Iterable[str] = ()
) -> dict[str, Any]:
    """`hsbot fetch` 가 읽는 targets.json 구조로 변환한다."""
    refs = list(refs)
    return {
        "_note": "hsbot search 가 자동 생성했습니다. 필요 없는 방송은 지우고 fetch 하세요.",
        "product_name": product_name or (refs[0].product_name if refs else ""),
        "lexicons": list(lexicons) or ["core_ko"],
        "targets": [
            {
                "channel": r.channel,
                "channel_name": r.display_channel,
                "product_key": r.product_key,
                "product_name": r.product_name,
                "tv_channel": r.tv_channel,
                "start_datetime": r.start_datetime,
                "end_datetime": r.end_datetime,
                "url": r.url or "",
            }
            for r in refs
        ],
    }


def save_refs(path: str, refs: Iterable[BroadcastRef]) -> None:
    import json

    with open(path, "w", encoding="utf-8") as f:
        json.dump([r.to_dict() for r in refs], f, ensure_ascii=False, indent=2)


def load_refs(path: str) -> list[BroadcastRef]:
    import json

    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        raw = raw.get("targets") or raw.get("refs") or [raw]
    return [BroadcastRef.from_dict(x) for x in raw]
