"""한국어 자막용 경량 토크나이저 / 숫자 추출기.

형태소 분석기(konlpy 등) 없이 stdlib만으로 동작한다.
어절에서 자주 붙는 조사를 잘라내는 수준의 근사 정규화이므로
100% 정확하지는 않지만, 채널 간 '상대 비교'에는 충분하다.
"""
from __future__ import annotations

import re
from collections import Counter

# 어절 끝에 붙는 흔한 조사/어미 (긴 것부터 제거)
_PARTICLES = (
    "이라고", "라고", "에서는", "에서", "으로는", "으로", "에게", "한테", "부터",
    "까지", "보다", "처럼", "만큼", "이라", "이나", "거나", "든지",
    "은", "는", "이", "가", "을", "를", "에", "와", "과", "도", "만", "의", "로", "요",
)

_TOKEN_RE = re.compile(r"[가-힣]+|[A-Za-z][A-Za-z0-9]*|\d+(?:[.,]\d+)*")
_HANGUL_RE = re.compile(r"[가-힣]")


def normalize(text: str) -> str:
    """비교용 정규화: 공백 압축 + 소문자화."""
    return re.sub(r"\s+", " ", text).strip().lower()


# 조사와 형태가 겹쳐 잘리면 안 되는 도메인 명사.
# 형태소 분석기가 없으므로 '최저가 → 최저'(가 = 주격조사) 같은 오절단을
# 규칙으로 막는다. 필요하면 register_protected()로 늘린다.
_PROTECTED: set[str] = {
    "최저가", "할인가", "판매가", "정상가", "방송가", "단독가", "특가", "파격가", "원가",
    "실구매가", "가격", "물가", "저가", "고가", "평가", "추가", "증가", "감가",
    "흡입력", "청소기", "생중계", "무이자", "사은품", "적립금", "배송비",
}


def register_protected(words) -> None:
    """조사 절단에서 보호할 단어를 추가한다."""
    _PROTECTED.update(w.lower() for w in words if w)


def strip_particle(word: str) -> str:
    """어절 끝 조사를 잘라낸다. 어간이 2글자 미만이 되거나 보호어면 원형 유지."""
    if not _HANGUL_RE.search(word) or word in _PROTECTED:
        return word
    for p in _PARTICLES:
        if word.endswith(p) and len(word) - len(p) >= 2:
            return word[: -len(p)]
    return word


def tokens(text: str, *, stem: bool = True) -> list[str]:
    out = []
    for t in _TOKEN_RE.findall(text.lower()):
        out.append(strip_particle(t) if stem else t)
    return out


def bigrams(toks: list[str]) -> list[str]:
    return [f"{a} {b}" for a, b in zip(toks, toks[1:])]


def token_counts(text: str, *, use_bigrams: bool = True, min_len: int = 2) -> Counter:
    toks = [t for t in tokens(text) if len(t) >= min_len]
    c = Counter(toks)
    if use_bigrams:
        c.update(bigrams(toks))
    return c


# ---------- 금액 / 비율 추출 ----------

_UNIT = {"조": 10**12, "억": 10**8, "만": 10**4, "천": 10**3, "백": 10**2}
_MONEY_PART = r"\d{1,4}(?:,\d{3})*(?:\.\d+)?\s*[조억만천백]?"
_MONEY_RE = re.compile(rf"((?:{_MONEY_PART}\s*)+)(?:원|원대)")
_PART_RE = re.compile(r"(\d{1,4}(?:,\d{3})*(?:\.\d+)?)\s*([조억만천백]?)")

_PERCENT_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*(?:%|퍼센트|프로)")
_MONTHS_RE = re.compile(r"(\d{1,2})\s*개월")


def extract_prices(text: str) -> list[int]:
    """'39만 8천원', '398,000원' 같은 복합 표현을 원 단위 정수로 합산한다."""
    out: list[int] = []
    for chunk in _MONEY_RE.findall(text):
        total = 0.0
        for num, unit in _PART_RE.findall(chunk):
            try:
                val = float(num.replace(",", ""))
            except ValueError:
                continue
            total += val * _UNIT.get(unit, 1)
        if 100 <= total <= 10**10:
            out.append(int(total))
    return out


def extract_percents(text: str) -> list[float]:
    return [float(x) for x in _PERCENT_RE.findall(text) if 0 < float(x) <= 100]


def extract_months(text: str) -> list[int]:
    return [int(x) for x in _MONTHS_RE.findall(text) if 0 < int(x) <= 60]
