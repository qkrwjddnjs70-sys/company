"""HAR에서 검색 스펙을 역추적하고, 검색→수집→분석까지 이어지는지 검증한다.

전부 네트워크 없이 돈다. 자막 수집 구간은 클라이언트 캐시를 미리 채워
HTTP 요청이 일어나지 않게 한 뒤 실행한다.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hsbot import cli
from hsbot.discover import dedupe, pick_best_group
from hsbot.sources import devtools as dt
from hsbot.sources.datahub import DataHubClient, DataHubConfig, DataHubError

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))
import make_search_har as msh   # noqa: E402


class HarFixture(unittest.TestCase):
    """합성 HAR을 만들어 임시 파일로 쓴다."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.har_path = os.path.join(cls.tmp.name, "search.har")
        with open(cls.har_path, "w", encoding="utf-8") as f:
            json.dump(msh.build_har("로보락"), f, ensure_ascii=False)
        cls.hits = dt.scan_har_search(cls.har_path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class ListingDetectionTest(HarFixture):
    def test_검색_응답을_하나_찾는다(self):
        self.assertEqual(len(self.hits), 1)

    def test_두_겹_아래에_있는_목록_경로를_찾는다(self):
        self.assertEqual(self.hits[0].candidates[0].path, "result.list")

    def test_낯선_필드명도_값의_생김새로_판정한다(self):
        # prdKey / goodsNm / onairStart 는 흔한 이름이 아니다.
        c = self.hits[0].candidates[0]
        self.assertEqual(c.key_keys[:1], ["prdKey"])
        self.assertEqual(c.name_keys[:1], ["goodsNm"])
        self.assertEqual(c.start_keys, ["onairStart"])
        self.assertEqual(c.end_keys, ["onairEnd"])

    def test_시작과_종료를_뒤바꾸지_않는다(self):
        c = self.hits[0].candidates[0]
        self.assertNotIn("onairEnd", c.start_keys)

    def test_채널_코드와_채널명을_구분한다(self):
        c = self.hits[0].candidates[0]
        self.assertIn("shopCode", c.channel_keys)
        self.assertIn("shopNm", c.channel_name_keys)

    def test_배너처럼_목록이_아닌_배열은_잡지_않는다(self):
        paths = {c.path for h in self.hits for c in h.candidates}
        self.assertNotIn("result.banners", paths)

    def test_썸네일은_상세URL로_쓰지_않는다(self):
        c = self.hits[0].candidates[0]
        self.assertEqual(c.url_keys[:1], ["detailUrl"])

    def test_검색과_무관한_응답은_후보에_없다(self):
        self.assertTrue(all("/api/v2/config" not in h.url for h in self.hits))


class SubtitleConfusionTest(HarFixture):
    """상품 목록을 자막으로 오인하지 않아야 한다."""

    def test_검색_응답은_자막_후보로_잡히지_않는다(self):
        # 상품명(한글)과 시각 필드가 있어 겉모습은 자막과 비슷하다.
        self.assertEqual(dt.scan_har(self.har_path), [])

    def test_목록_증거가_강하게_나온다(self):
        items = msh.build_items()
        self.assertGreaterEqual(dt.listing_evidence(items), 0.8)

    def test_진짜_자막_배열에는_목록_증거가_약하다(self):
        subs = [{"start_time": "00:00:0%d" % i, "text": "지금 주문하세요"} for i in range(9)]
        self.assertLess(dt.listing_evidence(subs), 0.5)


class SearchConfigTest(HarFixture):
    def test_검색어는_템플릿으로_바뀐다(self):
        sec = dt.build_search_section(self.hits[0])
        self.assertEqual(sec["endpoint"]["query"]["keyword"], "{keyword}")
        self.assertEqual(sec["observed_keyword"], "로보락")

    def test_pageSize는_page가_아니라_size로_템플릿화된다(self):
        # 'pageSize' 는 'page' 를 포함하므로 순서를 잘못 보면 페이지 번호로 잡힌다.
        q = dt.build_search_section(self.hits[0])["endpoint"]["query"]
        self.assertEqual(q["pageSize"], "{size}")
        self.assertEqual(q["page"], "{page}")

    def test_관측하지_못한_파라미터는_그대로_둔다(self):
        self.assertEqual(dt.build_search_section(self.hits[0])["endpoint"]["query"]["sort"], "recent")

    def test_설정에_search_엔드포인트가_들어간다(self):
        cfg = dt.build_config(self.hits[0].url, self.hits[0].headers, None,
                              search_hit=self.hits[0])
        self.assertIn("search", cfg["endpoints"])
        self.assertIn("search", cfg["mapping"])

    def test_쿠키는_설정에_들어가지_않는다(self):
        cfg = dt.build_config(self.hits[0].url, self.hits[0].headers, None,
                              search_hit=self.hits[0])
        self.assertNotIn("SESSIONID", json.dumps(cfg, ensure_ascii=False))


class ExtractRefsTest(HarFixture):
    def setUp(self):
        self.refs = dt.extract_refs(self.hits[0])

    def test_HAR_본문에서_방송_목록을_그대로_뽑는다(self):
        self.assertEqual(len(self.refs), len(msh.build_items()))

    def test_상품명과_채널명이_채워진다(self):
        r = self.refs[0]
        self.assertEqual(r.product_name, "로보락 S9 MAX Ultra")
        self.assertEqual(r.channel_name, "GS SHOP")

    def test_시작시각과_종료시각이_뒤바뀌지_않는다(self):
        r = self.refs[0]
        self.assertLess(r.start_datetime, r.end_datetime)
        self.assertAlmostEqual(r.duration_min, 60.0)

    def test_판매가를_읽는다(self):
        self.assertEqual(self.refs[0].price, 998_000)

    def test_중복_항목은_합쳐진다(self):
        self.assertEqual(len(dedupe(self.refs)), len(self.refs) - 1)

    def test_비교가_성립하는_모델이_자동_선택된다(self):
        model, group = pick_best_group(dedupe(self.refs))
        self.assertEqual(model, "S9 MAX ULTRA")
        self.assertGreaterEqual(len({r.channel for r in group}), 3)


class SearchEndpointErrorTest(unittest.TestCase):
    def _client(self, endpoints):
        return DataHubClient(
            DataHubConfig(base_url="https://x.test", endpoints=endpoints,
                          mapping={}, auth={}, cache_dir=None),
            api_key="unused",
        )

    def test_search_엔드포인트가_없으면_안내와_함께_실패한다(self):
        with self.assertRaises(DataHubError) as ctx:
            self._client({"subtitle": {"path": "/s"}}).search("로보락")
        self.assertIn("devtools", str(ctx.exception))

    def test_채울_수_없는_템플릿은_조용히_넘어가지_않는다(self):
        c = self._client({"search": {"path": "/s", "query": {"cat": "{category}"}}})
        with self.assertRaises(DataHubError) as ctx:
            c.search("로보락")
        self.assertIn("category", str(ctx.exception))

    def test_목록_경로를_못_찾으면_빈_결과가_아니라_오류다(self):
        c = self._client({"search": {"path": "/s"}})
        c.cfg.mapping = {"search": {"list_paths": ["없는.경로"]}}
        with self.assertRaises(DataHubError) as ctx:
            c.to_refs({"result": {"list": []}})
        self.assertIn("list_paths", str(ctx.exception))

    def test_자막이_0건인_것과_경로_오류를_구분한다(self):
        c = self._client({"search": {"path": "/s"}})
        c.cfg.mapping = {"search": {"list_paths": ["result.list"]}}
        self.assertEqual(c.to_refs({"result": {"list": []}}), [])


class CollectEndToEndTest(HarFixture):
    """검색(HAR) → 자막 수집(캐시) → 비교 분석 → HTML 까지 한 번에."""

    def setUp(self):
        self.work = tempfile.TemporaryDirectory()
        self.addCleanup(self.work.cleanup)
        self.cache = os.path.join(self.work.name, "cache")
        os.makedirs(self.cache, exist_ok=True)

        sec = dt.build_search_section(self.hits[0])
        self.cfg_dict = {
            "base_url": "https://datahub.hsmoa.com",
            "api_key_env": "HSBOT_TEST_COOKIE",
            "auth": {},
            "cache_dir": self.cache,
            "rate_limit_sec": 0.0,
            "endpoints": {
                "search": sec["endpoint"],
                "subtitle": {
                    "path": "/api/v1/product/{product_key}/subtitle",
                    "query": {"start_datetime": "{start_datetime}",
                              "end_datetime": "{end_datetime}"},
                },
            },
            "mapping": {
                "search": sec["mapping"],
                "segments": {
                    "list_paths": ["data.subtitles"],
                    "text_paths": ["text"],
                    "start_paths": ["start_sec"],
                    "end_paths": ["end_sec"],
                    "speaker_paths": ["speaker"],
                },
                "broadcast": {"product_name_paths": ["data.product_name"],
                              "price_paths": ["data.price"],
                              "channel_name_paths": ["data.channel_name"]},
            },
        }
        self.cfg_path = os.path.join(self.work.name, "datahub.json")
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(self.cfg_dict, f, ensure_ascii=False)

        self._prime_cache()

    def _prime_cache(self):
        """수집 대상 방송들의 자막 응답을 캐시에 미리 넣는다 (네트워크 차단)."""
        client = DataHubClient(DataHubConfig(**self.cfg_dict), api_key="unused")
        refs = dedupe(dt.extract_refs(self.hits[0]))
        _, group = pick_best_group(refs)
        # 시작시각이 없는 항목은 애초에 요청을 만들 수 없으므로 수집 대상이 아니다.
        self.group = [r for r in group if r.fetchable]
        # 모든 방송의 자막을 캐시에 넣는다. 하나라도 빠지면 그 테스트가
        # 실제 네트워크로 나가면서 재시도 백오프로 수십 초를 잡아먹는다.
        to_prime = [r for r in refs if r.fetchable]
        # 채널마다 다른 화법을 심어 비교 결과가 실제로 갈리는지 본다.
        lines = {
            "gsshop": "지금 이 시간 최저가입니다 매진 임박입니다",
            "cjonstyle": "흡입력 만 파스칼 직접 보여드리겠습니다",
            "lotte": "사은품까지 함께 드립니다 무이자 십이개월",
            "hyundai": "가족을 생각하면 이만한 선물이 없습니다",
        }
        for r in to_prime:
            body = {"data": {
                "product_name": r.product_name,
                "channel_name": r.channel_name,
                "price": r.price,
                "subtitles": [
                    {"start_sec": i * 8, "end_sec": i * 8 + 7,
                     "text": lines.get(r.channel, "좋은 상품입니다")}
                    for i in range(120)
                ],
            }}
            url = client._url("subtitle", {
                "product_key": r.product_key,
                "start_datetime": r.start_datetime,
                "end_datetime": r.end_datetime,
                "tv_channel": r.tv_channel or "",
            })
            path = os.path.join(self.cache, hashlib.sha256(url.encode()).hexdigest() + ".json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(body, f, ensure_ascii=False)

    def test_한_줄로_검색부터_리포트까지_이어진다(self):
        out = os.path.join(self.work.name, "collected.json")
        html = os.path.join(self.work.name, "report.html")
        rc = cli.main([
            "collect", "로보락", "--har", self.har_path, "--config", self.cfg_path,
            "--out", out, "--html", html, "--lexicon", "core_ko", "product_robot_vacuum",
        ])
        self.assertEqual(rc, 0)

        with open(out, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(len(saved), len(self.group))
        self.assertTrue(all(b["segments"] for b in saved))
        # 자동 선택된 모델은 하나여야 한다 (제품 차이가 섞이면 안 된다).
        self.assertEqual({b["product_name"] for b in saved}, {"로보락 S9 MAX Ultra"})

        with open(html, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("GS SHOP", page)
        self.assertIn("CJ온스타일", page)

    def test_모델을_직접_지정하면_그_그룹만_수집한다(self):
        out = os.path.join(self.work.name, "revo.json")
        rc = cli.main([
            "collect", "로보락", "--har", self.har_path, "--config", self.cfg_path,
            "--out", out, "--model", "Q REVO", "--no-analyze",
        ])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual({b["product_name"] for b in saved}, {"로보락 Q Revo Pro"})

    def test_같은_채널이_두_번_방송하면_날짜로_구분한다(self):
        out = os.path.join(self.work.name, "dup.json")
        rc = cli.main([
            "collect", "로보락", "--har", self.har_path, "--config", self.cfg_path,
            "--out", out, "--no-analyze",
        ])
        self.assertEqual(rc, 0)
        with open(out, encoding="utf-8") as f:
            names = [b["channel_name"] for b in json.load(f)]
        # GS SHOP 은 8월/9월 두 번 방송했다. 이름이 같으면 리포트에서 구분할 수 없다.
        self.assertEqual(len(names), len(set(names)))
        self.assertTrue(any(n.startswith("GS SHOP 08-") for n in names), names)

    def test_없는_모델을_지정하면_안내하고_멈춘다(self):
        rc = cli.main([
            "collect", "로보락", "--har", self.har_path, "--config", self.cfg_path,
            "--out", os.path.join(self.work.name, "x.json"), "--model", "없는모델",
        ])
        self.assertEqual(rc, 1)

    def test_search_명령은_targets_파일을_만든다(self):
        targets = os.path.join(self.work.name, "targets.json")
        rc = cli.main(["search", "로보락", "--har", self.har_path,
                       "--out-targets", targets, "--require-keyword"])
        self.assertEqual(rc, 0)
        with open(targets, encoding="utf-8") as f:
            spec = json.load(f)
        self.assertTrue(spec["targets"])
        # 비교가 성립하는 한 모델만 담겨야 한다.
        self.assertEqual({t["product_name"] for t in spec["targets"]}, {"로보락 S9 MAX Ultra"})


if __name__ == "__main__":
    unittest.main()
