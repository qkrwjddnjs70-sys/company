"""검색 결과 선별 계층(hsbot.discover) 회귀 테스트.

네트워크를 타지 않는 순수 함수만 다룬다.
"""
import unittest

from hsbot.discover import (
    BroadcastRef, comparable_groups, dedupe, filter_refs, group_by_model,
    model_key, pick_best_group, to_targets_spec,
)


def ref(channel, name, start, *, end="", key=None, chname=None, price=None):
    return BroadcastRef(
        product_key=key or f"{channel}_1",
        channel=channel,
        channel_name=chname,
        product_name=name,
        start_datetime=start,
        end_datetime=end,
        price=price,
    )


class ModelKeyTest(unittest.TestCase):
    def test_숫자를_포함한_모델코드부터_끝까지를_모델명으로_본다(self):
        self.assertEqual(model_key("로보락 S9 MAX Ultra"), "S9 MAX ULTRA")

    def test_괄호와_마케팅_문구는_버린다(self):
        self.assertEqual(
            model_key("[단독] 로보락 S9 MAX Ultra 로봇청소기 정품"), "S9 MAX ULTRA"
        )

    def test_같은_제품의_표기_차이를_흡수한다(self):
        a = model_key("[앵콜특가] 로보락 S9 MAX Ultra")
        b = model_key("로보락 S9 MAX Ultra 무료배송")
        self.assertEqual(a, b)

    def test_한_글자_모델코드도_인식한다(self):
        # 토크나이저가 한 글자 영문을 버리면 Q 가 사라져 '로보락 REVO PRO' 가 된다.
        self.assertEqual(model_key("로보락 Q Revo Pro"), "Q REVO PRO")
        self.assertEqual(model_key("로보락 Q Revo Pro 물걸레"), "Q REVO PRO")

    def test_수식어만_있는_이름은_모델코드로_보지_않는다(self):
        # 'AI' 를 모델 코드로 잡으면 전혀 다른 제품들이 한 그룹이 된다.
        self.assertEqual(model_key("삼성 비스포크 제트봇 AI"), "삼성 비스포크 제트봇")

    def test_다른_모델은_다른_키를_갖는다(self):
        self.assertNotEqual(model_key("로보락 S9 MAX Ultra"), model_key("로보락 Q Revo Pro"))

    def test_빈_이름은_빈_키(self):
        self.assertEqual(model_key(""), "")


class DedupeTest(unittest.TestCase):
    def test_채널_상품_시작시각이_같으면_하나로_본다(self):
        r = ref("gsshop", "로보락 S9", "2026-08-12T20:00:00+09:00")
        self.assertEqual(len(dedupe([r, ref("gsshop", "로보락 S9", "2026-08-12T20:00:00+09:00")])), 1)

    def test_시작시각이_다르면_다른_방송이다(self):
        rows = [
            ref("gsshop", "로보락 S9", "2026-08-12T20:00:00+09:00"),
            ref("gsshop", "로보락 S9", "2026-08-19T20:00:00+09:00"),
        ]
        self.assertEqual(len(dedupe(rows)), 2)


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            ref("gsshop", "로보락 S9 MAX Ultra", "2026-08-12T20:00:00+09:00",
                end="2026-08-12T21:00:00+09:00", chname="GS SHOP"),
            ref("cjonstyle", "로보락 Q Revo Pro", "2026-09-01T21:00:00+09:00",
                end="2026-09-01T21:20:00+09:00", chname="CJ온스타일"),
            ref("nsmall", "삼성 제트봇 AI", "2026-08-20T20:00:00+09:00",
                end="2026-08-20T21:00:00+09:00", chname="NS홈쇼핑"),
        ]

    def test_require는_모든_키워드를_포함해야_통과한다(self):
        got = filter_refs(self.rows, require=["로보락", "S9"])
        self.assertEqual([r.channel for r in got], ["gsshop"])

    def test_exclude는_하나라도_걸리면_제외한다(self):
        got = filter_refs(self.rows, exclude=["삼성"])
        self.assertEqual(len(got), 2)

    def test_채널은_코드와_이름_양쪽으로_찾는다(self):
        self.assertEqual(len(filter_refs(self.rows, channels=["CJ온"])), 1)
        self.assertEqual(len(filter_refs(self.rows, channels=["gsshop"])), 1)

    def test_기간_필터는_시작시각_기준(self):
        got = filter_refs(self.rows, since="2026-08-15")
        self.assertEqual({r.channel for r in got}, {"cjonstyle", "nsmall"})
        self.assertEqual(len(filter_refs(self.rows, until="2026-08-15")), 1)

    def test_편성길이_하한(self):
        got = filter_refs(self.rows, min_minutes=30)
        self.assertNotIn("cjonstyle", {r.channel for r in got})

    def test_시작시각을_모르는_방송은_기간필터에서_제외된다(self):
        rows = self.rows + [ref("lotte", "로보락 S9 MAX Ultra", "")]
        self.assertEqual(len(filter_refs(rows, since="2026-01-01")), 3)

    def test_결과는_시작시각_순으로_정렬된다(self):
        got = filter_refs(self.rows)
        self.assertEqual([r.start_datetime for r in got], sorted(r.start_datetime for r in got))

    def test_limit은_정렬_뒤에_적용된다(self):
        got = filter_refs(self.rows, limit=1)
        self.assertEqual(got[0].channel, "gsshop")


class GroupTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            ref("gsshop", "로보락 S9 MAX Ultra", "2026-08-12T20:00:00+09:00"),
            ref("cjonstyle", "로보락 S9 MAX Ultra", "2026-08-15T20:00:00+09:00"),
            ref("lotte", "로보락 S9 MAX Ultra", "2026-08-18T20:00:00+09:00"),
            ref("cjonstyle", "로보락 Q Revo Pro", "2026-09-01T20:00:00+09:00"),
            ref("lotte", "로보락 Q Revo Pro", "2026-09-03T20:00:00+09:00"),
            ref("nsmall", "로보락 먼지봉투", "2026-08-20T20:00:00+09:00"),
        ]

    def test_모델별로_묶인다(self):
        g = group_by_model(self.rows)
        self.assertEqual(len(g["S9 MAX ULTRA"]), 3)
        self.assertEqual(len(g["Q REVO PRO"]), 2)

    def test_한_채널뿐인_모델은_비교_대상이_아니다(self):
        models = [m for m, _ in comparable_groups(self.rows)]
        self.assertIn("S9 MAX ULTRA", models)
        self.assertNotIn("로보락 먼지봉투", models)

    def test_같은_채널의_재방송만_있으면_비교가_성립하지_않는다(self):
        rows = [
            ref("gsshop", "로보락 S9 MAX Ultra", "2026-08-12T20:00:00+09:00"),
            ref("gsshop", "로보락 S9 MAX Ultra", "2026-08-19T20:00:00+09:00"),
        ]
        self.assertEqual(comparable_groups(rows), [])

    def test_겹치는_채널이_가장_많은_모델이_기본_선택된다(self):
        model, group = pick_best_group(self.rows)
        self.assertEqual(model, "S9 MAX ULTRA")
        self.assertEqual(len(group), 3)

    def test_비교가_성립하지_않으면_None(self):
        self.assertIsNone(pick_best_group([ref("gsshop", "로보락 S9", "2026-08-12T20:00:00+09:00")]))


class RefTest(unittest.TestCase):
    def test_왕복_직렬화(self):
        r = ref("gsshop", "로보락 S9 MAX Ultra", "2026-08-12T20:00:00+09:00",
                end="2026-08-12T21:00:00+09:00", chname="GS SHOP", price=998000)
        back = BroadcastRef.from_dict(r.to_dict())
        self.assertEqual(back, r)

    def test_모르는_필드는_extra로_보존된다(self):
        back = BroadcastRef.from_dict({"product_key": "a_1", "channel": "a",
                                       "start_datetime": "", "썸네일": "x.jpg"})
        self.assertEqual(back.extra["썸네일"], "x.jpg")

    def test_편성길이(self):
        r = ref("gsshop", "x", "2026-08-12T20:00:00+09:00", end="2026-08-12T21:00:00+09:00")
        self.assertAlmostEqual(r.duration_min, 60.0)

    def test_시각이_없으면_편성길이는_미상이고_수집_불가다(self):
        r = ref("gsshop", "x", "")
        self.assertIsNone(r.duration_min)
        self.assertFalse(r.fetchable)

    def test_채널명이_없으면_코드로_표시한다(self):
        self.assertEqual(ref("gsshop", "x", "").display_channel, "gsshop")


class TargetsSpecTest(unittest.TestCase):
    def test_fetch가_읽는_구조로_변환된다(self):
        rows = [ref("gsshop", "로보락 S9 MAX Ultra", "2026-08-12T20:00:00+09:00",
                    chname="GS SHOP")]
        spec = to_targets_spec(rows, product_name="로보락", lexicons=["core_ko"])
        self.assertEqual(spec["product_name"], "로보락")
        self.assertEqual(spec["lexicons"], ["core_ko"])
        self.assertEqual(len(spec["targets"]), 1)
        self.assertEqual(spec["targets"][0]["channel_name"], "GS SHOP")
        self.assertEqual(spec["targets"][0]["start_datetime"], "2026-08-12T20:00:00+09:00")


if __name__ == "__main__":
    unittest.main()
