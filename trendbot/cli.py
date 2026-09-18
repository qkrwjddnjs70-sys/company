"""명령줄 인터페이스.

  python3 -m trendbot trend "로보락"                  # 3개년 월간 추이(연도 겹침)
  python3 -m trendbot spikes                          # 등록된 후보 키워드 중 급상승 찾기
  python3 -m trendbot webapp                          # 웹 UI 실행
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

from .envfile import load_dotenv
from .naver_api import NaverApiError, NaverDataLabClient, date_n_weeks_ago, date_n_years_ago
from .pool import PoolConfig, PoolConfigError
from .spike import rank_spikes
from .yearly import weekly_overlay, yearly_overlay

load_dotenv()


def cmd_trend(args) -> int:
    client = NaverDataLabClient()
    time_unit = "week" if args.weekly else "month"
    overlay_fn = weekly_overlay if args.weekly else yearly_overlay
    start = date_n_years_ago(args.years).isoformat()
    end = date.today().isoformat()
    try:
        series_map = client.search_trend(args.keywords, start_date=start, end_date=end, time_unit=time_unit)
    except NaverApiError as e:
        print(f"[실패] {e}", file=sys.stderr)
        return 1

    label = "주간" if args.weekly else "월간"
    for kw in args.keywords:
        series = series_map.get(kw)
        if series is None:
            print(f"'{kw}': 결과 없음", file=sys.stderr)
            continue
        overlay = overlay_fn(series)
        print(f"\n■ '{kw}' — {start} ~ {end} {label} 검색 비율(연도별)")
        for year, values in overlay["years"].items():
            cells = "  ".join(f"{v:5.1f}" if v is not None else "   · " for v in values)
            print(f"  {year}: {cells}")

    if args.out:
        payload = {kw: overlay_fn(series_map[kw]) for kw in args.keywords if kw in series_map}
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n저장: {args.out}")
    return 0


def cmd_spikes(args) -> int:
    try:
        pool = PoolConfig.load(args.config)
    except PoolConfigError as e:
        print(f"[실패] {e}", file=sys.stderr)
        return 1

    keywords = pool.all_keywords()
    if not keywords:
        print("등록된 후보 키워드가 없습니다. config/trendbot.json 에 categories/watchlist 를 채우세요.",
              file=sys.stderr)
        return 1

    weeks_needed = pool.spike["recent_weeks"] + pool.spike["baseline_weeks"] + 1
    start = date_n_weeks_ago(weeks_needed).isoformat()
    end = date.today().isoformat()

    client = NaverDataLabClient()
    try:
        series_map = client.search_trend(keywords, start_date=start, end_date=end, time_unit="week")
    except NaverApiError as e:
        print(f"[실패] {e}", file=sys.stderr)
        return 1

    keyword_ratios = {kw: s.ratios() for kw, s in series_map.items()}
    results = rank_spikes(
        keyword_ratios,
        recent_weeks=pool.spike["recent_weeks"],
        baseline_weeks=pool.spike["baseline_weeks"],
        min_growth_pct=pool.spike["min_growth_pct"],
        min_ratio_floor=pool.spike["min_ratio_floor"],
        keyword_labels=pool.keyword_labels(),
    )

    print(f"\n■ 급상승 키워드 — 후보 {len(keywords)}개 중 {len(results)}건 (최근 {pool.spike['recent_weeks']}주 "
          f"vs 직전 {pool.spike['baseline_weeks']}주)")
    for r in results:
        growth = "신규 급증" if r.is_new else f"+{r.growth_pct:.0f}%"
        labels = ", ".join(r.labels)
        print(f"  {growth:>10s}  {r.keyword:<20s} 최근 {r.recent_avg:5.1f} / 이전 {r.baseline_avg:5.1f}  [{labels}]")

    if args.out:
        payload = [
            {"keyword": r.keyword, "baseline_avg": r.baseline_avg, "recent_avg": r.recent_avg,
             "growth_pct": r.growth_pct, "is_new": r.is_new, "labels": r.labels}
            for r in results
        ]
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f"\n저장: {args.out}")
    return 0


def cmd_webapp(args) -> int:
    from .webapp import run

    run(host=args.host, port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="trendbot", description="네이버 검색 데이터로 홈쇼핑 소싱 인사이트 찾기")
    sub = p.add_subparsers(dest="cmd", required=True)

    t = sub.add_parser("trend", help="키워드의 최근 N개년 월간 추이(연도별 겹침)")
    t.add_argument("keywords", nargs="+", help='예: trend "로보락" "에어프라이어"')
    t.add_argument("--years", type=int, default=3)
    t.add_argument("--weekly", action="store_true", help="월간 대신 주간(ISO 1~53주) 비율로 조회")
    t.add_argument("--out", default=None, metavar="OUT.json")
    t.set_defaults(func=cmd_trend)

    s = sub.add_parser("spikes", help="등록된 후보 키워드 중 급상승 탐지")
    s.add_argument("--config", default=None, help="설정 파일 경로 (기본 config/trendbot.json)")
    s.add_argument("--out", default=None, metavar="OUT.json")
    s.set_defaults(func=cmd_spikes)

    w = sub.add_parser("webapp", help="웹 UI 실행 (키워드 추이 + 급상승 대시보드)")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8766)
    w.set_defaults(func=cmd_webapp)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
