"""소구축(axis) 사전 로딩 및 매칭."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

LEXICON_DIR = os.path.join(os.path.dirname(__file__), "lexicons")


@dataclass
class Axis:
    key: str
    label: str
    keywords: list[str]
    color: str = "#9fb3c8"
    desc: str = ""
    _regex: re.Pattern | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        # 긴 키워드를 먼저 매칭해야 '무료배송'이 '배송'으로 쪼개지지 않는다.
        kws = sorted({k.lower() for k in self.keywords if k}, key=len, reverse=True)
        self._regex = re.compile("|".join(re.escape(k) for k in kws)) if kws else None

    def hits(self, text: str) -> list[str]:
        """텍스트에서 이 축의 키워드 매칭 결과(중복 포함)를 돌려준다."""
        if self._regex is None:
            return []
        return self._regex.findall(text.lower())

    def count(self, text: str) -> int:
        return len(self.hits(text))


@dataclass
class Lexicon:
    name: str
    axes: dict[str, Axis]

    def __iter__(self):
        return iter(self.axes.values())

    @property
    def keys(self) -> list[str]:
        return list(self.axes)

    def merge(self, other: "Lexicon", name: str | None = None) -> "Lexicon":
        """두 사전을 합친다. 키가 겹치면 뒤쪽(other)이 이긴다."""
        return Lexicon(name or f"{self.name}+{other.name}", {**self.axes, **other.axes})


def load_lexicon(path_or_name: str) -> Lexicon:
    path = path_or_name
    if not os.path.exists(path):
        cand = os.path.join(LEXICON_DIR, f"{path_or_name}.json")
        if not os.path.exists(cand):
            raise FileNotFoundError(f"사전을 찾을 수 없습니다: {path_or_name}")
        path = cand
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    axes = {
        key: Axis(
            key=key,
            label=spec.get("label", key),
            keywords=spec.get("keywords", []),
            color=spec.get("color", "#9fb3c8"),
            desc=spec.get("desc", ""),
        )
        for key, spec in raw.get("axes", {}).items()
    }
    return Lexicon(raw.get("name", os.path.basename(path)), axes)


def load_combined(names: list[str]) -> Lexicon:
    if not names:
        raise ValueError("사전 이름이 비어 있습니다.")
    lex = load_lexicon(names[0])
    for n in names[1:]:
        lex = lex.merge(load_lexicon(n))
    lex.name = "+".join(names)
    return lex
