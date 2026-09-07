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
from .sources import devtools as dt


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


def _print_candidate(c, indent: str = "      ") -> None:
    print(f"{indent}배열 경로 : {c.path}  ({c.length}개, 점수 {c.score})")
    print(f"{indent}본문 필드 : {c.text_keys or '못 찾음'}")
    print(f"{indent}시각 필드 : {c.time_keys or '못 찾음'}")
    if c.speaker_keys:
        print(f"{indent}화자 필드 : {c.speaker_keys}")
    sample = json.dumps(c.sample, ensure_ascii=False)
    print(f"{indent}샘플      : {sample[:180]}{'...' if len(sample) > 180 else ''}")


def cmd_devtools(args) -> int:
    """F12 산출물(HAR / Copy as cURL)에서 API 스펙을 역추적한다."""
    url = headers = None
    candidate = body = None

    if args.har:
        hits = dt.scan_har(args.har, min_score=args.min_score)
        if not hits:
            print(
                "자막처럼 보이는 JSON 응답을 찾지 못했습니다.\n"
                "  · Network 탭에서 Fetch/XHR 필터를 켜고 자막 탭을 실제로 눌러본 뒤 HAR을 저장했는지 확인하세요.\n"
                "  · 응답 본문이 HAR에 포함되지 않은 경우도 있습니다(Chrome: 'Preserve log' 켜기).\n"
                f"  · 임계값을 낮춰 다시 보려면 --min-score 1 을 주세요.",
                file=sys.stderr,
            )
            return 1
        print(f"자막 후보 응답 {len(hits)}건 (점수순)\n")
        for i, h in enumerate(hits[: args.limit]):
            print(f"  [{i}] {h.method} {h.status}  {h.url[:150]}")
            _print_candidate(h.candidates[0])
            print()
        pick = hits[min(args.pick, len(hits) - 1)]
        url, headers, candidate = pick.url, pick.headers, pick.candidates[0]
        body = pick.body
        print(f"→ [{min(args.pick, len(hits) - 1)}]번을 기준으로 설정을 만듭니다.")

    elif args.curl:
        with open(args.curl, encoding="utf-8") as f:
            req = dt.parse_curl(f.read())
        url, headers = req.url, req.headers
        print(f"요청  : {req.method} {req.path}")
        print(f"호스트: {req.base_url}")
        print(f"쿼리  : {json.dumps(req.query, ensure_ascii=False)}")
        print(f"쿠키  : {'있음' if req.cookie else '없음'}")
        print("\n  ※ cURL만으로는 응답 스키마를 알 수 없습니다. "
              "필드 자동 추론까지 원하면 --har 을 쓰세요.")
    else:
        print("--har 또는 --curl 중 하나가 필요합니다.", file=sys.stderr)
        return 2

    # HAR에는 응답 본문이 들어 있으므로, 네트워크 요청 없이 바로 자막을 뽑을 수 있다.
    if args.extract:
        if not args.har:
            print("--extract 는 --har 과 함께 써야 합니다 (cURL에는 응답 본문이 없습니다).", file=sys.stderr)
            return 2
        try:
            bc = dt.extract_broadcast(
                pick, channel=args.channel, channel_name=args.channel_name,
                product_name=args.product, start_datetime=args.start, end_datetime=args.end,
            )
        except ValueError as e:
            print(f"[실패] {e}", file=sys.stderr)
            return 1
        print(f"\n[추출] {bc.display_channel} / {bc.product_name}")
        print(f"       자막 {len(bc.segments)}줄, 편성 {bc.duration_min:.0f}분")
        for s_ in bc.segments[:3]:
            print(f"         {s_.start_sec:7.1f}s  {s_.text[:52]}")
        if len(bc.segments) > 3:
            print(f"         ... (총 {len(bc.segments)}줄)")
        _ensure_parent(args.extract)
        save_broadcasts(args.extract, [bc])
        print(f"       저장: {args.extract}")

    cookie = (headers or {}).get("cookie")
    if cookie and not args.no_cookie:
        path = dt.save_cookie(cookie, args.cookie_out)
        print(f"\n쿠키 저장: {path}  (권한 0600, .gitignore 처리됨)")
        print(f'  export HSMOA_DATAHUB_COOKIE="$(cat {path})"')

    cfg = dt.build_config(url, headers or {}, candidate, product_key=args.product_key, body=body)
    _ensure_parent(args.out_config)
    if os.path.exists(args.out_config) and not args.force:
        print(f"\n[중단] {args.out_config} 가 이미 있습니다. 덮어쓰려면 --force 를 주세요.", file=sys.stderr)
        return 1
    with open(args.out_config, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"설정 생성: {args.out_config}")
    print("\n다음 단계:")
    print(f"  1) {args.out_config} 의 endpoints/mapping 을 눈으로 검토")
    print("  2) export HSMOA_DATAHUB_COOKIE=\"$(cat .secrets/datahub.cookie)\"")
    print("  3) python3 -m hsbot fetch --targets config/targets.json --out data/broadcasts.json")
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

    d = sub.add_parser("devtools", help="F12 HAR/cURL에서 API 스펙 역추적 + 설정 생성")
    g = d.add_mutually_exclusive_group(required=True)
    g.add_argument("--har", help="DevTools Network 탭에서 저장한 .har 파일")
    g.add_argument("--curl", help="'Copy as cURL (bash)' 내용을 담은 텍스트 파일")
    d.add_argument("--product-key", default=None, help="예: gsshop_1101476773 (경로 템플릿화에 사용)")
    d.add_argument("--out-config", default="config/datahub.json")
    d.add_argument("--cookie-out", default=os.path.join(".secrets", "datahub.cookie"))
    d.add_argument("--no-cookie", action="store_true", help="쿠키를 저장하지 않음")
    d.add_argument("--min-score", type=float, default=3.0)
    d.add_argument("--limit", type=int, default=5, help="후보를 몇 개까지 출력할지")
    d.add_argument("--pick", type=int, default=0, help="설정 생성에 쓸 후보 번호")
    d.add_argument("--force", action="store_true", help="기존 설정 덮어쓰기")
    d.add_argument("--extract", default=None, metavar="OUT.json",
                   help="HAR 본문에서 자막을 바로 추출해 정규화 JSON으로 저장 (네트워크 불필요)")
    d.add_argument("--channel", default=None)
    d.add_argument("--channel-name", default=None)
    d.add_argument("--product", default=None, help="상품명 (미지정 시 응답에서 추론)")
    d.add_argument("--start", default=None, help="방송 시작 ISO8601 (URL에 없으면 필수)")
    d.add_argument("--end", default=None)
    d.set_defaults(func=cmd_devtools)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
