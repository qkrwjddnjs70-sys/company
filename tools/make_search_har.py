#!/usr/bin/env python3
"""검색 응답이 담긴 합성 HAR을 만든다.

⚠️ 실제 홈쇼핑 API 응답이 아니다. 실제 스펙을 모르는 상태에서
   `hsbot search --har` / `hsbot devtools --har` 가 **문서 없는 낯선 응답에서도**
   목록·필드를 스스로 찾아내는지 검증하기 위한 것이다.

그래서 일부러 다음을 섞어 뒀다.
  · 필드명을 흔히 쓰는 이름 대신 살짝 다르게 (prdKey, goodsNm, onairStart ...)
  · 목록 배열을 두 겹 아래에 (result.list)
  · 자막이 아닌 배너/추천 배열도 같은 응답에 (오탐 방지 확인용)
  · 검색어와 무관한 상품, 모델이 다른 상품, 시작시각이 없는 상품
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

KST = "+09:00"
CHANNELS = [
    ("gsshop", "GS SHOP", "gsmyshop"),
    ("cjonstyle", "CJ온스타일", "cjonstyle"),
    ("lotte", "롯데홈쇼핑", "lotteimall"),
    ("hyundai", "현대홈쇼핑", "hmall"),
    ("nsmall", "NS홈쇼핑", "nsmall"),
]
# (모델 표기, 정상가, 판매가)
MODELS = [
    ("로보락 S9 MAX Ultra", 1_290_000, 998_000),
    ("로보락 Q Revo Pro", 990_000, 749_000),
]
NOISE = [
    ("삼성 비스포크 제트봇 AI", 1_190_000, 899_000),   # 검색어와 무관 — --require-keyword 로 걸러짐
    ("로보락 정품 전용 먼지봉투 6개입", 39_000, 29_000),  # 로보락이지만 방송이 아닌 부속품
]


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + KST


def build_items() -> list[dict]:
    """방송 목록. 채널×모델을 엇갈리게 배치해 '겹치는 모델' 판정을 시험한다."""
    base = datetime(2026, 8, 12, 20, 0, 0)
    items: list[dict] = []
    n = 0

    # S9 MAX Ultra — 4개 채널 (비교 성립, 가장 넓게 겹침)
    for i, (code, name, tv) in enumerate(CHANNELS[:4]):
        start = base + timedelta(days=i * 3, hours=(i % 3))
        items.append(_row(n := n + 1, code, name, tv, MODELS[0], start, 60))
    # Q Revo Pro — 2개 채널 (비교는 성립하지만 겹침이 좁음)
    for i, (code, name, tv) in enumerate(CHANNELS[1:3]):
        start = base + timedelta(days=20 + i * 2, hours=1)
        items.append(_row(n := n + 1, code, name, tv, MODELS[1], start, 50))
    # S9 MAX Ultra 재방송 (같은 채널 중복 — dedupe 대상 아님, 시각이 다름)
    items.append(_row(n := n + 1, *CHANNELS[0][:2], CHANNELS[0][2], MODELS[0],
                      base + timedelta(days=30), 40))
    # 완전 중복 항목 (dedupe 대상)
    items.append(dict(items[0]))
    # 잡음: 다른 브랜드 / 부속품
    for i, noise in enumerate(NOISE):
        code, name, tv = CHANNELS[4]
        items.append(_row(n := n + 1, code, name, tv, noise,
                          base + timedelta(days=5 + i), 60))
    # 시작시각이 없는 항목 (수집 불가 경고 확인용)
    broken = _row(n := n + 1, *CHANNELS[2][:2], CHANNELS[2][2], MODELS[0], base, 60)
    broken["onairStart"] = ""
    broken["onairEnd"] = ""
    items.append(broken)
    return items


def _row(n, code, name, tv, model, start: datetime, minutes: int) -> dict:
    title, list_price, sale_price = model
    return {
        # 일부러 흔한 이름을 피했다 — 자동 추론이 이름에만 의존하지 않는지 보려고.
        "prdKey": f"{code}_{1101470000 + n}",
        "goodsNm": title,
        "shopCode": tv,
        "shopNm": name,
        "onairStart": _iso(start),
        "onairEnd": _iso(start + timedelta(minutes=minutes)),
        "salePrice": sale_price,
        "listPrice": list_price,
        "detailUrl": f"https://datahub.hsmoa.com/product/{code}_{1101470000 + n}"
                     f"?tv_channel={tv}&start_datetime={_iso(start)}"
                     f"&end_datetime={_iso(start + timedelta(minutes=minutes))}&tab=subtitle",
        "thumb": "https://img.example/thumb.jpg",
    }


def build_har(keyword: str = "로보락") -> dict:
    body = {
        "code": 200,
        "message": "OK",
        "result": {
            "total": 0,
            "page": 1,
            # 목록은 두 겹 아래에 둔다 (경로 탐색을 실제로 하는지 확인)
            "list": build_items(),
            # 오탐 유도: 목록처럼 생겼지만 방송이 아닌 배열
            "banners": [
                {"id": 1, "title": "8월 가전 대전", "img": "https://img.example/b1.jpg"},
                {"id": 2, "title": "무이자 할부 안내", "img": "https://img.example/b2.jpg"},
                {"id": 3, "title": "신규 회원 쿠폰", "img": "https://img.example/b3.jpg"},
            ],
        },
    }
    body["result"]["total"] = len(body["result"]["list"])

    url = (
        "https://datahub.hsmoa.com/api/v2/search/broadcast"
        f"?keyword={keyword}&page=1&pageSize=50&sort=recent"
    )
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "hsbot make_search_har", "version": "0.1"},
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": url,
                        "headers": [
                            {"name": "cookie", "value": "SESSIONID=SYNTHETIC_NOT_REAL; ab=1"},
                            {"name": "user-agent", "value": "Mozilla/5.0 (synthetic)"},
                            {"name": "referer", "value": "https://datahub.hsmoa.com/search"},
                            {"name": "x-requested-with", "value": "XMLHttpRequest"},
                        ],
                    },
                    "response": {
                        "status": 200,
                        "content": {
                            "mimeType": "application/json",
                            "text": json.dumps(body, ensure_ascii=False),
                        },
                    },
                },
                # 검색과 무관한 응답도 하나 섞어 둔다 (필터링 확인)
                {
                    "request": {"method": "GET", "url": "https://datahub.hsmoa.com/api/v2/config",
                               "headers": []},
                    "response": {"status": 200, "content": {
                        "mimeType": "application/json",
                        "text": json.dumps({"theme": "dark", "features": ["subtitle", "search"]}),
                    }},
                },
            ],
        }
    }


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join("fixtures", "synthetic", "search.har")
    keyword = sys.argv[2] if len(sys.argv) > 2 else "로보락"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(build_har(keyword), f, ensure_ascii=False, indent=2)
    n = len(build_items())
    print(f"합성 검색 HAR 생성: {out}  (방송 {n}건, 검색어 {keyword!r})")
    print("  ⚠️ 실제 API 응답이 아닙니다. 스키마 자동추론 검증용입니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
