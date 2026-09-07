"""F12 산출물 역추적 모듈 테스트."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hsbot.sources import devtools as dt
from hsbot.sources.datahub import DataHubClient, DataHubConfig

SUB_PAYLOAD = {
    "result": {"code": 200},
    "data": {
        "product": {"name": "로보락 S9 MAX Ultra", "price": 398000},
        "subtitle_list": [
            {"seq": i, "start_time": f"00:00:{i:02d}", "end_time": f"00:00:{i + 2:02d}",
             "subtitle": t, "speaker": "쇼호스트"}
            for i, t in enumerate([
                "안녕하세요 로보락입니다", "흡입력 보시면 이렇게 다릅니다",
                "오늘 방송가 39만 8천원", "마감 임박입니다", "지금 바로 주문하세요",
            ])
        ],
    },
}
NOISE_PAYLOAD = {"data": {"banners": [{"id": i, "img": f"b{i}.png"} for i in range(6)]}}

SUB_URL = ("https://datahub.hsmoa.com/api/v2/product/gsshop_1101476773/subtitle"
           "?tv_channel=gsmyshop&start_datetime=2026-09-04T20%3A38%3A00%2B09%3A00"
           "&end_datetime=2026-09-04T21%3A38%3A00%2B09%3A00")

CURL = """curl 'https://datahub.hsmoa.com/api/v2/product/gsshop_1101476773/subtitle?tv_channel=gsmyshop' \\
  -H 'Cookie: SESSION=abc123; csrftoken=zzz' \\
  -H 'User-Agent: Mozilla/5.0' \\
  -H 'Referer: https://datahub.hsmoa.com/product/gsshop_1101476773' \\
  --compressed"""


def _har(entries):
    return {"log": {"version": "1.2", "entries": entries}}


def _entry(url, payload, headers=None):
    return {
        "request": {"method": "GET", "url": url,
                    "headers": [{"name": k, "value": v} for k, v in (headers or {}).items()]},
        "response": {"status": 200, "content": {"mimeType": "application/json",
                                                "text": json.dumps(payload, ensure_ascii=False)}},
    }


class TestCurlParsing(unittest.TestCase):
    def test_basic_fields(self):
        r = dt.parse_curl(CURL)
        self.assertEqual(r.method, "GET")
        self.assertEqual(r.base_url, "https://datahub.hsmoa.com")
        self.assertEqual(r.path, "/api/v2/product/gsshop_1101476773/subtitle")
        self.assertEqual(r.query["tv_channel"], "gsmyshop")
        self.assertIn("SESSION=abc123", r.cookie)

    def test_safe_headers_exclude_credentials(self):
        safe = dt.parse_curl(CURL).safe_headers()
        self.assertNotIn("cookie", safe)
        self.assertNotIn("authorization", safe)
        self.assertIn("user-agent", safe)

    def test_rejects_non_curl(self):
        with self.assertRaises(ValueError):
            dt.parse_curl("GET /foo HTTP/1.1")

    def test_data_flag_implies_post(self):
        r = dt.parse_curl("curl 'https://x.invalid/a' --data-raw '{}'")
        self.assertEqual(r.method, "POST")


class TestSchemaInference(unittest.TestCase):
    def test_finds_subtitle_array(self):
        cands = dt.find_array_candidates(SUB_PAYLOAD)
        top = cands[0]
        self.assertEqual(top.path, "data.subtitle_list")
        self.assertEqual(top.text_keys, ["subtitle"])
        self.assertEqual(top.speaker_keys, ["speaker"])

    def test_speaker_not_treated_as_body(self):
        self.assertNotIn("speaker", dt.find_array_candidates(SUB_PAYLOAD)[0].text_keys)

    def test_start_before_end(self):
        top = dt.find_array_candidates(SUB_PAYLOAD)[0]
        self.assertEqual(top.start_keys, ["start_time"])
        self.assertEqual(top.end_keys, ["end_time"])

    def test_noise_scores_low(self):
        noise = dt.find_array_candidates(NOISE_PAYLOAD)
        self.assertTrue(not noise or noise[0].score < 3.0)

    def test_meta_paths_skip_subtitle_array(self):
        meta = dt.find_meta_paths(SUB_PAYLOAD, skip_prefix="data.subtitle_list")
        self.assertEqual(meta["name"][0], "data.product.name")
        self.assertEqual(meta["price"][0], "data.product.price")


class TestHarScan(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "s.har")
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(_har([
                _entry("https://datahub.hsmoa.com/api/v2/banner", NOISE_PAYLOAD),
                _entry(SUB_URL, SUB_PAYLOAD, {"Cookie": "SESSION=abc", "User-Agent": "Mozilla/5.0"}),
            ]), f, ensure_ascii=False)

    def tearDown(self):
        self.dir.cleanup()

    def test_picks_subtitle_response_over_noise(self):
        hits = dt.scan_har(self.path)
        self.assertEqual(len(hits), 1)
        self.assertIn("/subtitle", hits[0].url)
        self.assertEqual(hits[0].candidates[0].path, "data.subtitle_list")

    def test_skips_base64_and_nonjson(self):
        p = os.path.join(self.dir.name, "b.har")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(_har([{
                "request": {"method": "GET", "url": "https://x.invalid/i.png", "headers": []},
                "response": {"status": 200, "content": {"mimeType": "image/png",
                                                        "encoding": "base64", "text": "AAAA"}},
            }]), f)
        self.assertEqual(dt.scan_har(p), [])


class TestConfigGeneration(unittest.TestCase):
    def setUp(self):
        self.cand = dt.find_array_candidates(SUB_PAYLOAD)[0]
        self.cfg = dt.build_config(
            SUB_URL, {"cookie": "SESSION=abc", "user-agent": "Mozilla/5.0"},
            self.cand, product_key="gsshop_1101476773", body=SUB_PAYLOAD,
        )

    def test_never_embeds_cookie(self):
        blob = json.dumps(self.cfg, ensure_ascii=False)
        self.assertNotIn("SESSION=abc", blob)
        self.assertEqual(self.cfg["auth"]["type"], "cookie")

    def test_path_and_query_templatized(self):
        ep = self.cfg["endpoints"]["subtitle"]
        self.assertEqual(ep["path"], "/api/v2/product/{product_key}/subtitle")
        self.assertEqual(ep["query"]["start_datetime"], "{start_datetime}")
        self.assertEqual(ep["query"]["tv_channel"], "{tv_channel}")

    def test_generated_config_actually_parses_response(self):
        """생성된 설정으로 실제 응답이 파싱되는지 폐루프 검증."""
        raw = dict(self.cfg)
        raw.pop("_note", None)
        client = DataHubClient(DataHubConfig(**raw), api_key="SESSION=abc")
        bc = client.to_broadcast(
            SUB_PAYLOAD, SUB_PAYLOAD, product_key="gsshop_1101476773",
            start_datetime="2026-09-04T20:38:00+09:00",
            end_datetime="2026-09-04T21:38:00+09:00", channel="gsshop",
        )
        self.assertEqual(len(bc.segments), 5)
        self.assertEqual(bc.product_name, "로보락 S9 MAX Ultra")
        self.assertEqual(bc.price, 398000)
        self.assertEqual(bc.segments[0].speaker, "쇼호스트")
        self.assertEqual(bc.segments[2].start_sec, 2.0)


class TestClientAuth(unittest.TestCase):
    def _client(self, auth, env=None):
        cfg = DataHubConfig(base_url="https://x.invalid", auth=auth,
                            endpoints={}, mapping={}, cache_dir=None)
        return DataHubClient(cfg, api_key=env)

    def test_cookie_header_sent(self):
        h = self._client({"type": "cookie"}, "SESSION=abc")._headers()
        self.assertEqual(h["Cookie"], "SESSION=abc")

    def test_extra_headers_override_defaults_without_duplication(self):
        h = self._client({"type": "cookie", "extra_headers": {"user-agent": "Mozilla/5.0"}},
                         "SESSION=abc")._headers()
        self.assertEqual(h["User-Agent"], "Mozilla/5.0")
        self.assertEqual(len([k for k in h if k.lower() == "user-agent"]), 1)

    def test_missing_credential_message_is_actionable(self):
        with self.assertRaises(Exception) as ctx:
            self._client({"type": "cookie", "cookie_env": "MY_COOKIE"}, None)._headers()
        self.assertIn("MY_COOKIE", str(ctx.exception))


class TestCookieStorage(unittest.TestCase):
    def test_saved_with_owner_only_permission(self):
        with tempfile.TemporaryDirectory() as d:
            p = dt.save_cookie("SESSION=abc", os.path.join(d, ".secrets", "c.cookie"))
            self.assertEqual(oct(os.stat(p).st_mode)[-3:], "600")
            with open(p, encoding="utf-8") as f:
                self.assertEqual(f.read().strip(), "SESSION=abc")


if __name__ == "__main__":
    unittest.main(verbosity=2)
