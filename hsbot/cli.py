"""명령줄 인터페이스.

  python -m hsbot url   <datahub_url>              # URL에서 수집 파라미터 추출
  python -m hsbot fetch --targets config/targets.json --out data/broadcasts.json
  python -m hsbot paste --file sub.txt --channel gsshop ... --out data/gs.json
  python -m hsbot analyze --input data/*.json --html out/report.html
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

from .compare import compare as compare_broadcasts
from .report import render as render_html, render_json
from .lexicon import load_combined
from .metrics import analyze as analyze_one
from .models import Broadcast, load_broadcasts, save_broadcasts
from .sources.datahub import DataHubClient, DataHubConfig, DataHubError, parse_datahub_url
from .sources.paste import load_paste_file


def _expand(paths: list[str]) -> list[str]:
    out: list[str] = []
    for p in paths:
        hits = sorted(glob.glob(p))
        out.extend(hits or [p])
    return out


def _ensure_parent(path: str) -> None:
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)


# ---------------- 명령 ----------------

def cmd_url(args) -> int:
    print(json.dumps(parse_datahub_url(args.url), ensure_ascii=False, indent=2))
    return 0


def cmd_fetch(args) -> int:
    with open(args.targets, encoding="utf-8") as f:
        spec = json.load(f)
    spec.pop("_note", None)
    targets = [t for t in spec.get("targets", []) if t.get("url")]
    if not targets:
        print("수집할 대상이 없습니다. targets[].url 을 채우세요.", file=sys.stderr)
        return 2

    client = DataHubClient(DataHubConfig.load(args.config))
    broadcasts: list[Broadcast] = []
    for t in targets:
        params = parse_datahub_url(t["url"])
        try:
            bc = client.fetch_broadcast(
                product_key=params["product_key"],
                start_datetime=t.get("start_datetime") or params.get("start_datetime", ""),
                end_datetime=t.get("end_datetime") or params.get("end_datetime", ""),
                tv_channel=t.get("tv_channel") or params.get("tv_channel"),
                channel=t.get("channel") or params.get("channel"),
                product_name=t.get("product_name") or spec.get("product_name"),
                use_cache=not args.no_cache,
            )
        except DataHubError as e:
            print(f"[실패] {t.get('channel_name') or params['product_key']}: {e}", file=sys.stderr)
            if args.strict:
                return 1
            continue
        if t.get("channel_name"):
            bc.channel_name = t["channel_name"]
        print(f"[수집] {bc.display_channel}: 자막 {len(bc.segments)}줄 / {bc.duration_min:.0f}분")
        broadcasts.append(bc)

    if not broadcasts:
        print("수집된 방송이 없습니다.", file=sys.stderr)
        return 1
    _ensure_parent(args.out)
    save_broadcasts(args.out, broadcasts)
    print(f"저장: {args.out} ({len(broadcasts)}건)")
    return 0


def cmd_paste(args) -> int:
    bc = load_paste_file(
        args.file,
        broadcast_id=args.id or f"{args.channel}:{args.start}",
        channel=args.channel,
        channel_name=args.channel_name,
        product_name=args.product,
        product_id=args.product_id,
        price=args.price,
        start_datetime=args.start,
        end_datetime=args.end,
    )
    print(f"[파싱] {bc.display_channel}: 자막 {len(bc.segments)}줄 / {bc.duration_min:.0f}분")
    _ensure_parent(args.out)
    save_broadcasts(args.out, [bc])
    print(f"저장: {args.out}")
    return 0


def cmd_analyze(args) -> int:
    broadcasts: list[Broadcast] = []
    for p in _expand(args.input):
        broadcasts.extend(load_broadcasts(p))
    if not broadcasts:
        print("분석할 방송이 없습니다.", file=sys.stderr)
        return 2
    empty = [b.display_channel for b in broadcasts if not b.segments]
    if empty:
        print(f"[경고] 자막이 비어 있는 방송: {', '.join(empty)}", file=sys.stderr)

    lex = load_combined(args.lexicon)
    metrics = [analyze_one(b, lex, bucket_sec=args.bucket_sec) for b in broadcasts]
    res = compare_broadcasts(broadcasts, metrics, spread_threshold=args.spread_threshold)

    print(f"\n■ {res.product_name} — 방송 {len(metrics)}건 비교")
    print(f"  사전: {lex.name} ({len(lex.axes)}축), 타임라인 {args.bucket_sec // 60}분 단위")
    print("\n  [발화 밀도]")
    for m in metrics:
        print(f"   - {m.channel_name:14s} {m.chars_per_min:7,.0f}자/분  {m.n_segments:5,}줄  반복 {m.repetition_index*100:.1f}%")
    print("\n  [전략이 갈린 축 · 편차 큰 순]")
    for a in res.axes[:6]:
        lead = res.by_id(a.leader).channel_name
        detail = "  ".join(f"{res.by_id(b).channel_name} {a.index[b]:.0f}" for b in res.ids)
        print(f"   - {a.label:10s} 편차×{a.spread:.2f}  1위 {lead:12s} | {detail}")

    if args.html:
        _ensure_parent(args.html)
        with open(args.html, "w", encoding="utf-8") as f:
            f.write(render_html(res, title=args.title, source_note=args.source_note))
        print(f"\nHTML 리포트: {args.html}")
    if args.json:
        _ensure_parent(args.json)
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(render_json(res))
        print(f"JSON 지표:   {args.json}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hsbot", description="홈쇼핑 방송 화법 비교 분석기")
    sub = p.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("url", help="DataHub URL에서 수집 파라미터 추출")
    u.add_argument("url")
    u.set_defaults(func=cmd_url)

    f = sub.add_parser("fetch", help="DataHub API로 자막 수집")
    f.add_argument("--targets", default="config/targets.json")
    f.add_argument("--config", default=None, help="API 설정 (기본 config/datahub.json)")
    f.add_argument("--out", default="data/broadcasts.json")
    f.add_argument("--no-cache", action="store_true")
    f.add_argument("--strict", action="store_true", help="한 건이라도 실패하면 중단")
    f.set_defaults(func=cmd_fetch)

    s = sub.add_parser("paste", help="붙여넣기 자막 텍스트를 정규화 JSON으로")
    s.add_argument("--file", required=True)
    s.add_argument("--channel", required=True)
    s.add_argument("--channel-name", default=None)
    s.add_argument("--product", required=True)
    s.add_argument("--product-id", default=None)
    s.add_argument("--price", type=int, default=None)
    s.add_argument("--start", required=True, help="ISO8601 (예: 2026-09-04T20:38:00+09:00)")
    s.add_argument("--end", default=None)
    s.add_argument("--id", default=None)
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_paste)

    a = sub.add_parser("analyze", help="정규화 JSON들을 비교 분석")
    a.add_argument("--input", nargs="+", required=True)
    a.add_argument("--lexicon", nargs="+", default=["core_ko"])
    a.add_argument("--bucket-sec", type=int, default=300)
    a.add_argument("--spread-threshold", type=float, default=1.35)
    a.add_argument("--html", default=None)
    a.add_argument("--json", default=None)
    a.add_argument("--title", default=None)
    a.add_argument("--source-note", default="")
    a.set_defaults(func=cmd_analyze)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
