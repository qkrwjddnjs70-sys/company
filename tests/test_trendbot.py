"""trendbot 회귀 테스트 (stdlib unittest + unittest.mock만 사용)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trendbot.envfile import load_dotenv
from trendbot.naver_api import NaverApiError, NaverDataLabClient, TrendPoint, TrendSeries, _chunked
from trendbot.pool import PoolConfig, PoolConfigError
from trendbot.spike import compute_spike, rank_spikes
from trendbot.yearly import yearly_overlay


class TestLoadDotenv(unittest.TestCase):
    def _write(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".env")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        self.addCleanup(os.remove, path)
        return path

    def test_missing_file_returns_zero(self):
        self.assertEqual(load_dotenv("/no/such/.env"), 0)

    def test_parses_keys_skips_comments_and_blanks(self):
        path = self._write(
            '# 주석\n'
            '\n'
            'TRENDBOT_NAVER_CLIENT_ID=abc123\n'
            'TRENDBOT_NAVER_CLIENT_SECRET="s3cret"\n'
        )
        os.environ.pop("TRENDBOT_NAVER_CLIENT_ID", None)
        os.environ.pop("TRENDBOT_NAVER_CLIENT_SECRET", None)
        self.addCleanup(os.environ.pop, "TRENDBOT_NAVER_CLIENT_ID", None)
        self.addCleanup(os.environ.pop, "TRENDBOT_NAVER_CLIENT_SECRET", None)
        loaded = load_dotenv(path)
        self.assertEqual(loaded, 2)
        self.assertEqual(os.environ["TRENDBOT_NAVER_CLIENT_ID"], "abc123")
        self.assertEqual(os.environ["TRENDBOT_NAVER_CLIENT_SECRET"], "s3cret")

    def test_existing_env_var_takes_precedence(self):
        path = self._write("TRENDBOT_NAVER_CLIENT_ID=from_file\n")
        os.environ["TRENDBOT_NAVER_CLIENT_ID"] = "from_shell"
        self.addCleanup(os.environ.pop, "TRENDBOT_NAVER_CLIENT_ID", None)
        load_dotenv(path)
        self.assertEqual(os.environ["TRENDBOT_NAVER_CLIENT_ID"], "from_shell")


class TestSpike(unittest.TestCase):
    def test_growth_percent(self):
        # 이전 8주 평균 10, 최근 2주 평균 20 → +100%
        ratios = [10.0] * 8 + [20.0] * 2
        result = compute_spike(ratios, recent_weeks=2, baseline_weeks=8)
        baseline_avg, recent_avg, growth_pct, is_new = result
        self.assertAlmostEqual(baseline_avg, 10.0)
        self.assertAlmostEqual(recent_avg, 20.0)
        self.assertAlmostEqual(growth_pct, 100.0)
        self.assertFalse(is_new)

    def test_new_keyword_when_baseline_zero(self):
        ratios = [0.0] * 8 + [5.0] * 2
        _, _, growth_pct, is_new = compute_spike(ratios, recent_weeks=2, baseline_weeks=8)
        self.assertIsNone(growth_pct)
        self.assertTrue(is_new)

    def test_insufficient_history_returns_none(self):
        self.assertIsNone(compute_spike([1.0, 2.0], recent_weeks=2, baseline_weeks=8))

    def test_rank_spikes_filters_and_sorts(self):
        keyword_ratios = {
            "급등": [10.0] * 8 + [30.0] * 2,       # +200%
            "완만": [10.0] * 8 + [11.0] * 2,       # +10%, 문턱(30%) 미달
            "신규": [0.0] * 8 + [5.0] * 2,          # 신규 급증 → 최상단
            "잡음": [0.1] * 8 + [0.5] * 2,          # 증가율은 크지만 절대량이 floor 미만
        }
        results = rank_spikes(
            keyword_ratios, recent_weeks=2, baseline_weeks=8,
            min_growth_pct=30.0, min_ratio_floor=1.0,
        )
        keywords = [r.keyword for r in results]
        self.assertEqual(keywords, ["신규", "급등"])  # 신규가 최상단, 완만/잡음은 제외

    def test_rank_spikes_attaches_labels(self):
        keyword_ratios = {"급등": [10.0] * 8 + [30.0] * 2}
        results = rank_spikes(
            keyword_ratios, min_growth_pct=30.0, min_ratio_floor=1.0,
            keyword_labels={"급등": ["생활가전", "관심 키워드"]},
        )
        self.assertEqual(results[0].labels, ["생활가전", "관심 키워드"])


class TestYearlyOverlay(unittest.TestCase):
    def test_pivots_by_year_and_month(self):
        series = TrendSeries(keyword="로봇청소기", points=[
            TrendPoint(period="2024-01-01", ratio=10.0),
            TrendPoint(period="2024-02-01", ratio=12.0),
            TrendPoint(period="2025-01-01", ratio=20.0),
        ])
        overlay = yearly_overlay(series)
        self.assertEqual(overlay["months"], list(range(1, 13)))
        self.assertEqual(overlay["years"]["2024"][0], 10.0)
        self.assertEqual(overlay["years"]["2024"][1], 12.0)
        self.assertIsNone(overlay["years"]["2024"][2])  # 3월은 관측치 없음
        self.assertEqual(overlay["years"]["2025"][0], 20.0)


class TestPoolConfig(unittest.TestCase):
    def _write(self, data: dict) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        self.addCleanup(os.remove, path)
        return path

    def test_load_missing_raises(self):
        with self.assertRaises(PoolConfigError):
            PoolConfig.load("/no/such/file.json")

    def test_all_keywords_dedup_across_categories_and_watchlist(self):
        path = self._write({
            "categories": [
                {"name": "생활가전", "seed_keywords": ["로봇청소기", "가습기"]},
                {"name": "캠핑", "seed_keywords": ["캠핑의자"]},
            ],
            "watchlist": ["가습기", "온열매트"],
        })
        pool = PoolConfig.load(path)
        self.assertEqual(pool.all_keywords(), ["로봇청소기", "가습기", "캠핑의자", "온열매트"])

    def test_keyword_labels(self):
        path = self._write({
            "categories": [{"name": "생활가전", "seed_keywords": ["로봇청소기"]}],
            "watchlist": ["로봇청소기"],
        })
        pool = PoolConfig.load(path)
        self.assertEqual(pool.keyword_labels()["로봇청소기"], ["생활가전", "관심 키워드"])

    def test_spike_defaults_merged_with_overrides(self):
        path = self._write({"spike": {"min_growth_pct": 50}})
        pool = PoolConfig.load(path)
        self.assertEqual(pool.spike["min_growth_pct"], 50)
        self.assertEqual(pool.spike["recent_weeks"], 2)  # 기본값 유지


class TestNaverDataLabClient(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.client = NaverDataLabClient(
            client_id="id", client_secret="secret",
            cache_dir=self.tmpdir, rate_limit_sec=0.0,
        )

    def test_chunked(self):
        self.assertEqual(list(_chunked([1, 2, 3, 4, 5, 6], 5)), [[1, 2, 3, 4, 5], [6]])

    def test_missing_credentials_raises(self):
        client = NaverDataLabClient(client_id="", client_secret="", cache_dir=None)
        with self.assertRaises(NaverApiError):
            client.search_trend(["로보락"], start_date="2024-01-01", end_date="2024-12-01")

    def test_search_trend_splits_keywords_into_groups(self):
        response = {"results": [
            {"title": "로보락", "data": [{"period": "2024-01-01", "ratio": 12.3}]},
        ]}
        fake = mock.Mock()
        fake.read.return_value = json.dumps(response).encode("utf-8")
        fake.__enter__ = lambda s: fake
        fake.__exit__ = lambda s, *a: False
        with mock.patch("urllib.request.urlopen", return_value=fake) as m:
            out = self.client.search_trend(
                ["로보락"], start_date="2024-01-01", end_date="2024-12-01", time_unit="month"
            )
            m.assert_called_once()
            req = m.call_args[0][0]
            payload = json.loads(req.data.decode("utf-8"))
            self.assertEqual(payload["keywordGroups"], [{"groupName": "로보락", "keywords": ["로보락"]}])
        self.assertEqual(out["로보락"].points[0].ratio, 12.3)

    def test_second_call_same_day_uses_cache(self):
        response = {"results": [{"title": "가습기", "data": []}]}
        fake = mock.Mock()
        fake.read.return_value = json.dumps(response).encode("utf-8")
        fake.__enter__ = lambda s: fake
        fake.__exit__ = lambda s, *a: False
        with mock.patch("urllib.request.urlopen", return_value=fake) as m:
            self.client.search_trend(["가습기"], start_date="2024-01-01", end_date="2024-12-01")
            self.client.search_trend(["가습기"], start_date="2024-01-01", end_date="2024-12-01")
            self.assertEqual(m.call_count, 1)  # 두 번째는 캐시로 응답


if __name__ == "__main__":
    unittest.main()
