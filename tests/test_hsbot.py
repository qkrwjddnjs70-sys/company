"""hsbot 회귀 테스트 (stdlib unittest만 사용)."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hsbot import textutil as tu
from hsbot.compare import compare, distinctive_terms
from hsbot.lexicon import load_combined, load_lexicon
from hsbot.metrics import analyze
from hsbot.models import Broadcast, Segment, load_broadcasts, save_broadcasts
from hsbot.sources.base import dig, first_present, to_seconds
from hsbot.sources.datahub import DataHubClient, DataHubConfig, parse_datahub_url
from hsbot.sources.paste import parse_subtitle_text

START = "2026-09-04T20:38:00+09:00"
END = "2026-09-04T21:38:00+09:00"


class TestTextUtil(unittest.TestCase):
    def test_compound_prices(self):
        self.assertEqual(tu.extract_prices("오늘 39만 8천원"), [398000])
        self.assertEqual(tu.extract_prices("정상가 1,290,000원"), [1290000])
        self.assertEqual(tu.extract_prices("3만9천8백원에"), [39800])

    def test_price_noise_filtered(self):
        # 100원 미만 잡음은 버린다
        self.assertEqual(tu.extract_prices("단돈 5원"), [])

    def test_percent_and_months(self):
        self.assertEqual(tu.extract_percents("30% 할인, 45프로 세일"), [30.0, 45.0])
        self.assertEqual(tu.extract_percents("200% 확실"), [])   # 100 초과 제외
        self.assertEqual(tu.extract_months("12개월 무이자"), [12])

    def test_particle_stripping(self):
        self.assertEqual(tu.strip_particle("로보락이"), "로보락")
        self.assertEqual(tu.strip_particle("흡입력은"), "흡입력")
        self.assertEqual(tu.strip_particle("가격"), "가격")   # 2글자 미만 어간 방지

    def test_bigrams_included(self):
        c = tu.token_counts("마감 임박 마감 임박")
        self.assertEqual(c["마감 임박"], 2)


class TestLexicon(unittest.TestCase):
    def setUp(self):
        self.lex = load_combined(["core_ko", "product_robot_vacuum"])

    def test_axes_loaded(self):
        self.assertIn("PRICE", self.lex.axes)
        self.assertIn("SPEC_SUCTION", self.lex.axes)

    def test_longest_match_wins(self):
        # '무료배송'이 '배송'으로 쪼개지지 않아야 한다
        hits = self.lex.axes["PROMO"].hits("무료배송 해드립니다")
        self.assertIn("무료배송", hits)

    def test_case_insensitive(self):
        self.assertTrue(self.lex.axes["CTA"].hits("ARS로 주문하세요"))

    def test_merge_precedence(self):
        a = load_lexicon("core_ko")
        b = load_lexicon("product_robot_vacuum")
        merged = a.merge(b)
        self.assertEqual(len(merged.axes), len(a.axes) + len(b.axes))


class TestPasteParser(unittest.TestCase):
    def test_hms_timestamps(self):
        bc = parse_subtitle_text(
            "00:00:10 안녕하세요\n00:01:20 오늘 39만 8천원입니다\n",
            broadcast_id="t", channel="gs", product_name="P",
            start_datetime=START, end_datetime=END,
        )
        self.assertEqual(len(bc.segments), 2)
        self.assertEqual(bc.segments[1].start_sec, 80.0)
        self.assertAlmostEqual(bc.duration_min, 60.0)

    def test_bracket_and_range(self):
        bc = parse_subtitle_text(
            "[00:00:05 - 00:00:09] 마감 임박\n",
            broadcast_id="t", channel="gs", product_name="P", start_datetime=START,
        )
        self.assertEqual(bc.segments[0].start_sec, 5.0)
        self.assertEqual(bc.segments[0].end_sec, 9.0)

    def test_absolute_datetime_lines(self):
        bc = parse_subtitle_text(
            "2026-09-04 20:43:00 가격 보시죠\n",
            broadcast_id="t", channel="gs", product_name="P", start_datetime=START,
        )
        self.assertEqual(bc.segments[0].start_sec, 300.0)

    def test_untimed_lines_spread(self):
        bc = parse_subtitle_text(
            "\n".join(f"줄{i}" for i in range(5)),
            broadcast_id="t", channel="gs", product_name="P",
            start_datetime=START, end_datetime=END,
        )
        self.assertEqual(len(bc.segments), 5)
        self.assertLess(bc.segments[0].start_sec, bc.segments[-1].start_sec)

    def test_speaker_split(self):
        bc = parse_subtitle_text(
            "00:00:01 쇼호스트: 안녕하세요\n",
            broadcast_id="t", channel="gs", product_name="P", start_datetime=START,
        )
        self.assertEqual(bc.segments[0].speaker, "쇼호스트")
        self.assertEqual(bc.segments[0].text, "안녕하세요")


def _mk(bid: str, lines: list[tuple[int, str]], *, channel="ch") -> Broadcast:
    return Broadcast(
        broadcast_id=bid, channel=channel, channel_name=bid, product_name="로보락 S9",
        start_datetime=START, end_datetime=END,
        segments=[Segment(start_sec=float(t), text=x) for t, x in lines],
    )


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.lex = load_combined(["core_ko", "product_robot_vacuum"])

    def test_per_minute_normalization(self):
        """길이가 2배여도 밀도가 같으면 분당 지표는 같아야 한다."""
        short = Broadcast(
            broadcast_id="s", channel="c", product_name="P",
            start_datetime="2026-09-04T20:00:00+09:00", end_datetime="2026-09-04T20:30:00+09:00",
            segments=[Segment(start_sec=i * 60.0, text="최저가입니다") for i in range(30)],
        )
        long = Broadcast(
            broadcast_id="l", channel="c", product_name="P",
            start_datetime="2026-09-04T20:00:00+09:00", end_datetime="2026-09-04T21:00:00+09:00",
            segments=[Segment(start_sec=i * 60.0, text="최저가입니다") for i in range(60)],
        )
        a, b = analyze(short, self.lex), analyze(long, self.lex)
        self.assertAlmostEqual(a.axes["PRICE"].hits_per_min, b.axes["PRICE"].hits_per_min, places=3)
        self.assertNotEqual(a.axes["PRICE"].hits, b.axes["PRICE"].hits)

    def test_first_hit_and_peak(self):
        m = analyze(_mk("x", [(10, "안녕하세요"), (900, "마감 임박"), (910, "마감 임박")]), self.lex)
        self.assertEqual(m.axes["URGENCY"].first_hit_sec, 900.0)
        self.assertEqual(m.axes["URGENCY"].peak_bucket_idx, 3)   # 900초 / 300초 버킷

    def test_segment_coverage(self):
        m = analyze(_mk("x", [(0, "최저가"), (10, "안녕하세요")]), self.lex)
        self.assertAlmostEqual(m.axes["PRICE"].segment_coverage, 0.5)

    def test_numeric_extraction(self):
        m = analyze(_mk("x", [(0, "39만 8천원 30% 12개월 무이자")]), self.lex)
        self.assertEqual(m.numeric.lowest_price, 398000)
        self.assertEqual(m.numeric.max_percent, 30.0)
        self.assertEqual(m.numeric.max_installment_months, 12)
        self.assertEqual(m.numeric.first_price_sec, 0.0)

    def test_timeline_covers_duration(self):
        m = analyze(_mk("x", [(0, "테스트")]), self.lex, bucket_sec=600)
        self.assertEqual(len(m.timeline), 6)   # 60분 / 10분

    def test_empty_segments_safe(self):
        bc = Broadcast(broadcast_id="e", channel="c", product_name="P",
                       start_datetime=START, end_datetime=END, segments=[])
        m = analyze(bc, self.lex)
        self.assertEqual(m.n_segments, 0)
        self.assertEqual(m.chars_per_min, 0.0)


class TestCompare(unittest.TestCase):
    def setUp(self):
        self.lex = load_combined(["core_ko"])
        self.a = _mk("A", [(i * 10, "최저가 할인 특가") for i in range(30)], channel="a")
        self.b = _mk("B", [(i * 10, "흡입력 보시면 이렇게") for i in range(30)], channel="b")
        self.ms = [analyze(self.a, self.lex), analyze(self.b, self.lex)]

    def test_index_centered_on_mean(self):
        res = compare([self.a, self.b], self.ms)
        price = next(x for x in res.axes if x.key == "PRICE")
        self.assertGreater(price.index["A"], 150)
        self.assertLess(price.index["B"], 50)
        self.assertEqual(price.leader, "A")

    def test_axes_sorted_by_spread(self):
        res = compare([self.a, self.b], self.ms)
        spreads = [x.spread for x in res.axes]
        self.assertEqual(spreads, sorted(spreads, reverse=True))

    def test_distinctive_excludes_shared_terms(self):
        x = _mk("X", [(0, "그래서 최저가 최저가 최저가 최저가")])
        y = _mk("Y", [(0, "그래서 흡입력 흡입력 흡입력 흡입력")])
        d = distinctive_terms([x, y], min_count=2)
        top_x = [t for t, _, _ in d["X"]]
        self.assertIn("최저가", top_x)
        self.assertNotIn("그래서", top_x[:1])

    def test_single_broadcast_does_not_crash(self):
        res = compare([self.a], [self.ms[0]])
        self.assertEqual(len(res.metrics), 1)
        self.assertTrue(all(v == 100.0 for a in res.axes for v in a.index.values()))


class TestSourcesBase(unittest.TestCase):
    def test_to_seconds_variants(self):
        self.assertEqual(to_seconds("00:05:12"), 312)
        self.assertEqual(to_seconds(312), 312)
        self.assertEqual(to_seconds(312000), 312)      # ms 자동 판별
        self.assertAlmostEqual(to_seconds("5:12.500"), 312.5)

    def test_dig_and_first_present(self):
        obj = {"data": {"items": [{"text": "hi"}]}}
        self.assertEqual(dig(obj, "data.items.0.text"), "hi")
        self.assertIsNone(dig(obj, "data.nope.0"))
        self.assertEqual(first_present(obj, ["a.b", "data.items.0.text"]), "hi")


class TestDataHubMapping(unittest.TestCase):
    """API 스펙이 바뀌어도 설정만으로 흡수되는지 확인."""

    CFG = DataHubConfig(
        base_url="https://example.invalid",
        auth={},
        endpoints={"subtitle": {"path": "/s/{product_key}", "query": {}}},
        mapping={"segments": {
            "list_paths": ["payload.captions"],
            "text_paths": ["body"],
            "start_paths": ["ts"],
        }},
        cache_dir=None,
    )

    def test_custom_schema_mapping(self):
        client = DataHubClient(self.CFG, api_key="x")
        raw = {"payload": {"captions": [
            {"ts": "00:00:30", "body": "최저가입니다"},
            {"ts": "00:01:00", "body": ""},          # 빈 줄은 버려야 함
            {"ts": "00:02:00", "body": "마감 임박"},
        ]}}
        bc = client.to_broadcast(
            raw, {}, product_key="gsshop_1", start_datetime=START,
            end_datetime=END, channel="gsshop", product_name="P",
        )
        self.assertEqual(len(bc.segments), 2)
        self.assertEqual(bc.segments[0].start_sec, 30.0)
        self.assertEqual(bc.source, "datahub_api")

    def test_missing_list_path_raises_actionable_error(self):
        client = DataHubClient(self.CFG, api_key="x")
        with self.assertRaises(Exception) as ctx:
            client.to_broadcast({"unexpected": 1}, {}, product_key="p",
                                start_datetime=START, end_datetime=END, channel="c")
        self.assertIn("list_paths", str(ctx.exception))

    def test_url_parsing(self):
        p = parse_datahub_url(
            "https://datahub.hsmoa.com/product/gsshop_1101476773"
            "?tv_channel=gsmyshop&start_datetime=2026-09-04T20%3A38%3A00%2B09%3A00&tab=subtitle"
        )
        self.assertEqual(p["product_key"], "gsshop_1101476773")
        self.assertEqual(p["channel"], "gsshop")
        self.assertEqual(p["tv_channel"], "gsmyshop")


class TestRoundTrip(unittest.TestCase):
    def test_save_load_preserves_segments(self):
        bc = _mk("R", [(0, "최저가"), (60, "마감 임박")])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "b.json")
            save_broadcasts(p, [bc])
            back = load_broadcasts(p)
        self.assertEqual(len(back), 1)
        self.assertEqual([s.text for s in back[0].segments], ["최저가", "마감 임박"])
        self.assertEqual(back[0].broadcast_id, "R")


if __name__ == "__main__":
    unittest.main(verbosity=2)
