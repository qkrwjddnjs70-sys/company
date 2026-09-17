"""급상승 탐지에 쓸 후보 키워드 풀 — 카테고리 시드 + 직접 등록한 관심 키워드.

네이버는 실시간 급상승 검색어 API를 2021년에 폐지했다. 그래서 "무엇이 뜨는지"는
API로 자동 발견할 수 없고, 이 설정 파일에 미리 등록해 둔 후보 안에서만 찾을 수
있다. 여기서 말하는 '카테고리'는 네이버 쇼핑의 공식 분류 코드가 아니라, 사용자가
소싱 목적에 맞게 스스로 묶어 둔 키워드 그룹일 뿐이다.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

DEFAULT_CONFIG_PATH = os.path.join("config", "trendbot.json")

DEFAULT_SPIKE = {
    "recent_weeks": 2,
    "baseline_weeks": 8,
    "min_growth_pct": 30.0,
    "min_ratio_floor": 1.0,
}


class PoolConfigError(RuntimeError):
    pass


@dataclass
class Category:
    name: str
    seed_keywords: list[str] = field(default_factory=list)


@dataclass
class PoolConfig:
    categories: list[Category] = field(default_factory=list)
    watchlist: list[str] = field(default_factory=list)
    spike: dict = field(default_factory=lambda: dict(DEFAULT_SPIKE))

    @classmethod
    def load(cls, path: str | None = None) -> "PoolConfig":
        path = path or os.environ.get("TRENDBOT_CONFIG") or DEFAULT_CONFIG_PATH
        if not os.path.exists(path):
            example = os.path.join("config", "trendbot.example.json")
            raise PoolConfigError(
                f"설정 파일이 없습니다: {path}\n"
                f"  {example} 을 {path} 로 복사한 뒤 카테고리/관심 키워드를 채우세요."
            )
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        raw.pop("_note", None)
        categories = [Category(name=c["name"], seed_keywords=list(c.get("seed_keywords", [])))
                      for c in raw.get("categories", [])]
        spike = dict(DEFAULT_SPIKE)
        spike.update(raw.get("spike", {}))
        return cls(categories=categories, watchlist=list(raw.get("watchlist", [])), spike=spike)

    def all_keywords(self) -> list[str]:
        """카테고리 시드 + 관심 키워드를 합쳐 중복을 제거한다(등장 순서 보존)."""
        seen: dict[str, None] = {}
        for cat in self.categories:
            for kw in cat.seed_keywords:
                seen.setdefault(kw, None)
        for kw in self.watchlist:
            seen.setdefault(kw, None)
        return list(seen)

    def keyword_labels(self) -> dict[str, list[str]]:
        """키워드 → 소속 라벨 목록. 관심 키워드는 '관심 키워드'로 표시한다."""
        out: dict[str, list[str]] = {}
        for cat in self.categories:
            for kw in cat.seed_keywords:
                out.setdefault(kw, []).append(cat.name)
        for kw in self.watchlist:
            out.setdefault(kw, []).append("관심 키워드")
        return out
