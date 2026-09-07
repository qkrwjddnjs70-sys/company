"""검증용 합성 자막 생성기.

⚠️ 실제 홈쇼핑 방송 데이터가 아닙니다.
   파이프라인이 '채널별 화법 차이'를 실제로 검출하는지 확인하기 위해
   서로 다른 성향을 심어 놓은 가짜 자막을 결정적(seed 고정)으로 만듭니다.
"""
from __future__ import annotations

import os
import random
from datetime import datetime, timedelta

OUT_DIR = os.path.join("fixtures", "synthetic")

POOL = {
    "PRICE": [
        "오늘 방송가 {p}원입니다", "정상가 129만원이 오늘은 {p}원",
        "이 가격 다시 없습니다, 최저가예요", "{d}% 할인 들어갑니다",
        "실구매가 기준으로 {p}원이에요", "가격표 다시 보여드릴게요",
        "이보다 저렴하게 사실 수 없습니다", "방송 단독가로 준비했습니다",
    ],
    "PROMO": [
        "사은품으로 전용 세제 6개월분 드립니다", "무료배송에 설치까지 무료예요",
        "{m}개월 무이자 할부 가능합니다", "추가 구성으로 걸레 세트 하나 더",
        "카드할인까지 중복 적용됩니다", "포인트 적립도 챙겨가세요",
        "풀구성 패키지로만 준비했습니다",
    ],
    "URGENCY": [
        "마감 임박입니다 서두르세요", "수량이 얼마 안 남았습니다",
        "이 시간 지나면 이 가격 끝납니다", "벌써 매진 임박이에요",
        "오늘만 이 조건입니다", "마지막 수량 들어갑니다",
        "지금 안 하시면 놓치십니다", "선착순으로 마감됩니다",
    ],
    "CTA": [
        "지금 바로 전화 주문 눌러주세요", "화면 하단 번호로 연결하시면 됩니다",
        "자동주문 ARS가 더 빠릅니다", "모바일 앱에서도 주문 가능해요",
        "상담원 연결 지금 하세요", "장바구니 담고 결제까지 한 번에",
    ],
    "TRUST": [
        "글로벌 판매량 1위 브랜드입니다", "누적 리뷰 만 건 넘었습니다",
        "정품 공식 수입 제품이에요", "A/S 2년 보증됩니다",
        "특허 받은 기술입니다", "고객 만족도 최고 등급입니다",
    ],
    "DEMO": [
        "화면 보시면 이렇게 깨끗해집니다", "직접 시연해 보겠습니다",
        "여기 보이시죠 먼지가 싹 사라집니다", "한번 비교해 보시겠습니다",
        "지금 보시는 게 실제 상황입니다", "테스트 결과 보여드릴게요",
    ],
    "EMOTION": [
        "청소하느라 고생하셨잖아요", "맞벌이 부부에게 정말 편합니다",
        "아이 있는 집은 더 필요하세요", "반려동물 털 때문에 힘드셨죠",
        "이제 청소 스트레스에서 벗어나세요", "주말에 여유 시간이 생깁니다",
    ],
    "COMPARE": [
        "기존 모델과는 차원이 다릅니다", "타사 제품 대비 확실히 낫습니다",
        "일반 청소기와 비교가 안 됩니다", "업계 최초로 적용된 기술이에요",
        "이전 모델 쓰시던 분들도 놀라십니다",
    ],
    "RISK": [
        "부담없이 받아보시고 반품 가능합니다", "환불 걱정 안 하셔도 됩니다",
        "무상 교환 보장해 드립니다", "안심하고 주문하세요",
    ],
    "SPEC_SUCTION": [
        "흡입력이 무려 만 파스칼입니다", "머리카락 엉킴 없는 브러시예요",
        "미세먼지까지 잡아냅니다", "먼지 흡입력 보시면 압도적입니다",
    ],
    "SPEC_MOP": [
        "물걸레가 자동세척 됩니다", "온수로 걸레를 세척하고 열풍 건조까지",
        "물때랑 냄새 걱정 없습니다", "살균 세척 기능이 들어갔어요",
    ],
    "SPEC_DOCK": [
        "도크에서 먼지통 자동비움 됩니다", "급수 배수 전부 자동이에요",
        "손 안 대고 관리됩니다", "스테이션 하나로 끝납니다",
    ],
    "SPEC_NAV": [
        "라이다 센서로 집 구조를 매핑합니다", "장애물 회피가 정확합니다",
        "AI가 카펫을 인식해서 리프팅합니다", "구석 모서리까지 파고듭니다",
        "문턱도 가볍게 넘어갑니다",
    ],
    "SPEC_FORM": [
        "소음이 조용합니다 밤에도 돌리세요", "높이가 얇아서 소파 밑까지 들어갑니다",
        "디자인도 군더더기 없습니다",
    ],
    "SPEC_APP": [
        "앱에서 예약 청소 설정하시면 됩니다", "스마트폰으로 원격 제어됩니다",
        "음성 명령도 지원합니다",
    ],
    "SPEC_BATTERY": [
        "배터리로 60평까지 가동됩니다", "충전 한 번에 180분 사용 가능합니다",
        "물통 용량이 넉넉합니다",
    ],
    "FILLER": [
        "네 그렇습니다", "자 그러면 다음으로 넘어가 볼까요",
        "잠시만요", "정말 그렇죠", "예 맞습니다", "한번 볼까요",
    ],
}

# 채널별 성향 가중치 (합성 시나리오)
CHANNELS = {
    "gsshop": {
        "name": "GS SHOP",
        "desc": "가격·긴급 압박형",
        "w": {"PRICE": 3.2, "URGENCY": 3.0, "CTA": 2.2, "PROMO": 1.6, "TRUST": 0.8,
              "DEMO": 0.9, "EMOTION": 0.8, "COMPARE": 0.7, "RISK": 0.5,
              "SPEC_SUCTION": 1.0, "SPEC_MOP": 0.8, "SPEC_DOCK": 0.7, "SPEC_NAV": 0.6,
              "SPEC_FORM": 0.4, "SPEC_APP": 0.3, "SPEC_BATTERY": 0.4, "FILLER": 2.0},
        "price": [398000, 429000, 1290000],
    },
    "cjonstyle": {
        "name": "CJ온스타일",
        "desc": "스펙·시연 설득형",
        "w": {"PRICE": 1.3, "URGENCY": 1.0, "CTA": 1.2, "PROMO": 1.2, "TRUST": 2.0,
              "DEMO": 3.0, "EMOTION": 1.4, "COMPARE": 2.0, "RISK": 1.2,
              "SPEC_SUCTION": 2.4, "SPEC_MOP": 2.4, "SPEC_DOCK": 2.0, "SPEC_NAV": 2.4,
              "SPEC_FORM": 1.4, "SPEC_APP": 1.2, "SPEC_BATTERY": 1.2, "FILLER": 1.6},
        "price": [419000, 1290000],
    },
    "lotte": {
        "name": "롯데홈쇼핑",
        "desc": "혜택·안심 구성형",
        "w": {"PRICE": 1.8, "URGENCY": 1.4, "CTA": 1.6, "PROMO": 3.2, "TRUST": 1.6,
              "DEMO": 1.2, "EMOTION": 2.2, "COMPARE": 0.9, "RISK": 2.4,
              "SPEC_SUCTION": 1.2, "SPEC_MOP": 1.4, "SPEC_DOCK": 1.2, "SPEC_NAV": 1.0,
              "SPEC_FORM": 0.8, "SPEC_APP": 0.8, "SPEC_BATTERY": 0.8, "FILLER": 1.8},
        "price": [409000, 34000, 1290000],
    },
}

# 방송 진행에 따른 단계별 축 가중치 (초반 설명 → 후반 압박)
def phase_boost(pos: float) -> dict[str, float]:
    return {
        "URGENCY": 0.3 + 2.4 * pos ** 2,
        "CTA": 0.4 + 2.0 * pos ** 1.5,
        "PRICE": 0.7 + 1.3 * pos,
        "PROMO": 0.8 + 0.9 * pos,
        "DEMO": 1.8 - 1.0 * pos,
        "SPEC_SUCTION": 1.7 - 0.9 * pos,
        "SPEC_MOP": 1.7 - 0.9 * pos,
        "SPEC_NAV": 1.7 - 0.9 * pos,
        "SPEC_DOCK": 1.5 - 0.7 * pos,
        "EMOTION": 1.4 - 0.5 * pos,
    }


def make(channel: str, *, minutes: int = 60, gap_sec: int = 8, seed: int = 7) -> str:
    rng = random.Random(seed + sum(map(ord, channel)))
    spec = CHANNELS[channel]
    n = minutes * 60 // gap_sec
    lines = []
    for i in range(n):
        pos = i / max(n - 1, 1)
        boost = phase_boost(pos)
        keys, weights = [], []
        for k, base in spec["w"].items():
            keys.append(k)
            weights.append(max(0.05, base * boost.get(k, 1.0)))
        k = rng.choices(keys, weights=weights)[0]
        tmpl = rng.choice(POOL[k])
        text = tmpl.format(
            p=f"{rng.choice(spec['price']):,}",
            d=rng.choice([20, 30, 35, 40, 45]),
            m=rng.choice([3, 6, 12]),
        )
        t = i * gap_sec
        lines.append(f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d} {text}")
    return "\n".join(lines)


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    start = datetime.fromisoformat("2026-09-04T20:38:00+09:00")
    for ch, spec in CHANNELS.items():
        body = make(ch)
        header = (
            f"# ⚠️ 합성(가짜) 데이터입니다 — 실제 {spec['name']} 방송 자막이 아닙니다.\n"
            f"# 목적: hsbot 파이프라인 검증. 심어둔 성향: {spec['desc']}\n"
            f"# 생성기: tools/make_synthetic_fixtures.py (seed 고정)\n"
        )
        path = os.path.join(OUT_DIR, f"{ch}_robotvac.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(header + body + "\n")
        print(f"생성: {path} ({len(body.splitlines())}줄)")
    print(f"\n방송 시작 기준시각: {start.isoformat()} (편성 60분 가정)")


if __name__ == "__main__":
    main()
