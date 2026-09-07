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
import unicodedata

from . import discover as dc
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


def _pad(s: str, width: int) -> str:
    """한글은 터미널에서 두 칸을 차지한다. 그 폭을 세어 정렬을 맞춘다.

    파이썬의 `f"{s:12s}"` 는 글자 수만 세므로 한글이 섞이면 표가 어긋난다.
    """
    s = str(s)
    w = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)
    if w <= width:
        return s + " " * (width - w)
    # 넘치면 폭 기준으로 자른다
    out, acc = [], 0
    for c in s:
        cw = 2 if unicodedata.east_asian_width(c) in "WF" else 1
        if acc + cw > width:
            break
        out.append(c)
        acc += cw
    return "".join(out) + " " * (width - acc)


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


def _discover(args) -> list[dc.BroadcastRef]:
    """검색 결과를 얻는다. HAR이 있으면 네트워크 없이, 없으면 API로."""
    if getattr(args, "har", None):
        hits = dt.scan_har_search(args.har, min_score=args.min_score)
        if not hits:
            raise DataHubError(
                "HAR에서 방송 목록처럼 보이는 응답을 찾지 못했습니다.\n"
                "  · 브라우저에서 실제로 키워드 검색을 한 뒤 HAR을 저장했는지 확인하세요.\n"
                "  · 임계값을 낮추려면 --min-score 3 을 주세요."
            )
        pick = hits[min(args.pick, len(hits) - 1)]
        print(f"[HAR] {pick.method} {pick.status}  {pick.url[:120]}")
        print(f"      목록 경로 {pick.candidates[0].path} / {pick.candidates[0].length}건 "
              f"(점수 {pick.candidates[0].score})")
        return dt.extract_refs(pick)

    client = DataHubClient(DataHubConfig.load(args.config))
    return client.search(
        args.keyword,
        pages=args.pages,
        size=args.size,
        start_datetime=args.since or "",
        end_datetime=args.until or "",
        use_cache=not args.no_cache,
    )


def _select(args, refs: list[dc.BroadcastRef]) -> list[dc.BroadcastRef]:
    """검색 결과를 비교 가능한 후보로 좁힌다."""
    return dc.filter_refs(
        dc.dedupe(refs),
        require=([args.keyword] if getattr(args, "keyword", None) and args.require_keyword else [])
        + list(args.require or []),
        exclude=list(args.exclude or []),
        channels=list(args.channel or []),
        since=args.since,
        until=args.until,
        min_minutes=args.min_minutes,
        limit=args.limit,
    )


def _print_groups(groups: dict[str, list[dc.BroadcastRef]], *, min_channels: int = 2) -> None:
    print(f"\n  [모델별 묶음]  ※ 채널 {min_channels}곳 이상이어야 비교가 성립합니다")
    for model, g in groups.items():
        chans = sorted({r.display_channel for r in g})
        mark = "✓" if len(chans) >= min_channels else "·"
        print(f"   {mark} {_pad(model or '(미상)', 22)} 방송 {len(g):2d}건 / 채널 {len(chans)}곳  {', '.join(chans)}")


def cmd_search(args) -> int:
    try:
        refs = _discover(args)
    except DataHubError as e:
        print(f"[실패] {e}", file=sys.stderr)
        return 1

    print(f"\n■ '{args.keyword}' 검색 — 원본 {len(refs)}건")
    picked = _select(args, refs)
    if not picked:
        print("  조건에 맞는 방송이 없습니다. --require/--since/--channel 조건을 완화해 보세요.", file=sys.stderr)
        return 1
    print(f"  필터 통과 {len(picked)}건")

    unfetchable = [r for r in picked if not r.fetchable]
    if unfetchable:
        print(f"  [경고] 방송 시작시각이 없어 자막 수집이 불가능한 항목 {len(unfetchable)}건 "
              f"(예: {unfetchable[0].product_name[:30]})", file=sys.stderr)

    groups = dc.group_by_model(picked)
    _print_groups(groups, min_channels=args.min_channels)

    print("\n  [방송 목록]")
    for r in picked[: args.show]:
        dur = f"{r.duration_min:.0f}분" if r.duration_min else "  ?  "
        print(f"   - {r.start_datetime[:16] or '(시각없음)':16s} {_pad(r.display_channel, 14)} {dur:>5s}  {r.product_name[:44]}")
    if len(picked) > args.show:
        print(f"   ... 외 {len(picked) - args.show}건 (--show 로 더 보기)")

    if args.out_refs:
        _ensure_parent(args.out_refs)
        dc.save_refs(args.out_refs, picked)
        print(f"\n방송 목록 저장: {args.out_refs}")
    if args.out_targets:
        best = dc.pick_best_group(picked, min_channels=args.min_channels)
        # 시작시각이 없는 방송은 요청 자체를 만들 수 없으므로 대상에서 뺀다.
        chosen = [r for r in (best[1] if best else picked) if r.fetchable]
        if best:
            print(f"\ntargets 에는 비교가 성립하는 모델 '{best[0]}' 만 담았습니다 ({len(chosen)}건).")
        else:
            print(f"\n[주의] 2개 채널 이상 겹치는 모델이 없어 전체 {len(chosen)}건을 담았습니다. "
                  f"이대로 비교하면 제품 차이가 섞입니다.", file=sys.stderr)
        _ensure_parent(args.out_targets)
        with open(args.out_targets, "w", encoding="utf-8") as f:
            json.dump(dc.to_targets_spec(chosen, product_name=args.keyword,
                                         lexicons=args.lexicon), f, ensure_ascii=False, indent=2)
        print(f"수집 대상 저장: {args.out_targets}")
        print(f"  → python3 -m hsbot fetch --targets {args.out_targets} --out data/broadcasts.json")
    return 0


def cmd_collect(args) -> int:
    """검색 → 자막 수집 → 비교 분석을 한 번에."""
    try:
        refs = _discover(args)
    except DataHubError as e:
        print(f"[실패] {e}", file=sys.stderr)
        return 1

    picked = _select(args, refs)
    print(f"\n■ '{args.keyword}' — 검색 {len(refs)}건 → 필터 통과 {len(picked)}건")
    if not picked:
        print("  조건에 맞는 방송이 없습니다.", file=sys.stderr)
        return 1

    groups = dc.group_by_model(picked)
    _print_groups(groups, min_channels=args.min_channels)

    if args.model:
        chosen = [r for r in picked if args.model.upper() in (r.model_key or "").upper()]
        label = args.model
        if not chosen:
            print(f"\n'{args.model}' 에 해당하는 모델이 없습니다. 위 목록에서 골라 --model 로 주세요.",
                  file=sys.stderr)
            return 1
    elif args.all_models:
        chosen, label = picked, "전체"
    else:
        best = dc.pick_best_group(picked, min_channels=args.min_channels)
        if not best:
            print(f"\n[중단] 채널 {args.min_channels}곳 이상에서 판 모델이 없어 비교가 성립하지 않습니다.\n"
                  f"  · 기간을 넓혀 보세요 (--since/--until)\n"
                  f"  · 모델을 섞어서라도 보려면 --all-models 를 주세요(제품 차이가 섞입니다)",
                  file=sys.stderr)
            return 1
        chosen, label = best[1], best[0]
        print(f"\n→ 자동 선택: '{label}' ({len(chosen)}건)")

    skipped = [r for r in chosen if not r.fetchable]
    chosen = [r for r in chosen if r.fetchable]
    for r in skipped:
        print(f"[건너뜀] 시작시각 없음: {r.display_channel} {r.product_name[:30]}", file=sys.stderr)
    if not chosen:
        print("수집 가능한 방송이 없습니다.", file=sys.stderr)
        return 1

    # 같은 채널이 같은 상품을 여러 번 방송했으면 리포트에서 구분이 안 된다.
    # 이때만 날짜를 붙여 이름을 갈라준다 (한 번뿐이면 이름을 건드리지 않는다).
    seen_channels: dict[str, int] = {}
    for r in chosen:
        seen_channels[r.display_channel] = seen_channels.get(r.display_channel, 0) + 1
    dupes = {c for c, n in seen_channels.items() if n > 1}

    client = DataHubClient(DataHubConfig.load(args.config))
    broadcasts: list[Broadcast] = []
    for r in chosen:
        try:
            bc = client.fetch_ref(r, use_cache=not args.no_cache)
            if r.display_channel in dupes:
                bc.channel_name = f"{r.display_channel} {r.start_datetime[5:10]}"
        except DataHubError as e:
            print(f"[실패] {r.display_channel} {r.start_datetime[:16]}: {e}", file=sys.stderr)
            if args.strict:
                return 1
            continue
        print(f"[수집] {_pad(bc.display_channel, 14)} {r.start_datetime[:16]}  "
              f"자막 {len(bc.segments):,}줄 / {bc.duration_min:.0f}분")
        broadcasts.append(bc)

    if not broadcasts:
        print("수집된 방송이 없습니다.", file=sys.stderr)
        return 1
    _ensure_parent(args.out)
    save_broadcasts(args.out, broadcasts)
    print(f"저장: {args.out} ({len(broadcasts)}건)")

    if args.no_analyze:
        print(f"  → python3 -m hsbot analyze --input {args.out} --lexicon {' '.join(args.lexicon)}")
        return 0

    ns = argparse.Namespace(
        input=[args.out], lexicon=args.lexicon, bucket_sec=args.bucket_sec,
        spread_threshold=args.spread_threshold, html=args.html, json=args.json,
        title=args.title or f"{args.keyword} — {label}",
        source_note=f"hsbot collect '{args.keyword}' / 모델 {label} / 방송 {len(broadcasts)}건",
    )
    return cmd_analyze(ns)


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
        print(f"   - {_pad(m.channel_name, 16)} {m.chars_per_min:7,.0f}자/분  {m.n_segments:5,}줄  반복 {m.repetition_index*100:.1f}%")
    print("\n  [전략이 갈린 축 · 편차 큰 순]")
    for a in res.axes[:6]:
        lead = res.by_id(a.leader).channel_name
        detail = "  ".join(f"{res.by_id(b).channel_name} {a.index[b]:.0f}" for b in res.ids)
        print(f"   - {_pad(a.label, 14)} 편차×{a.spread:.2f}  1위 {_pad(lead, 14)} | {detail}")

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

    search_pick = None
    if args.har:
        hits = dt.scan_har(args.har, min_score=args.min_score)
        search_hits = dt.scan_har_search(args.har, min_score=args.search_min_score)

        if not hits and not search_hits:
            print(
                "자막도 방송 목록도 찾지 못했습니다.\n"
                "  · Network 탭에서 Fetch/XHR 필터를 켜고 자막 탭 / 검색을 실제로 눌러본 뒤 HAR을 저장했는지 확인하세요.\n"
                "  · 응답 본문이 HAR에 포함되지 않은 경우도 있습니다(Chrome: 'Preserve log' 켜기).\n"
                "  · 임계값을 낮춰 다시 보려면 --min-score 1 --search-min-score 3 을 주세요.",
                file=sys.stderr,
            )
            return 1

        if search_hits:
            print(f"검색(방송 목록) 후보 응답 {len(search_hits)}건 (점수순)\n")
            for i, h in enumerate(search_hits[: args.limit]):
                c = h.candidates[0]
                print(f"  [{i}] {h.method} {h.status}  {h.url[:150]}")
                print(f"      목록 경로 : {c.path}  ({c.length}건, 점수 {c.score})")
                print(f"      상품키    : {c.key_keys or '못 찾음'}")
                print(f"      상품명    : {c.name_keys or '못 찾음'}")
                print(f"      방송시각  : 시작 {c.start_keys or '못 찾음'} / 종료 {c.end_keys or '없음'}")
                print(f"      채널      : {(c.channel_keys + c.channel_name_keys) or '못 찾음'}")
                print(f"      검색어    : {h.guess_keyword() or '못 찾음'}")
                print()
            search_pick = search_hits[min(args.search_pick, len(search_hits) - 1)]

        if hits:
            print(f"자막 후보 응답 {len(hits)}건 (점수순)\n")
            for i, h in enumerate(hits[: args.limit]):
                print(f"  [{i}] {h.method} {h.status}  {h.url[:150]}")
                _print_candidate(h.candidates[0])
                print()
            pick = hits[min(args.pick, len(hits) - 1)]
            url, headers, candidate = pick.url, pick.headers, pick.candidates[0]
            body = pick.body
            print(f"→ 자막은 [{min(args.pick, len(hits) - 1)}]번을 기준으로 설정을 만듭니다.")
        else:
            print("※ 이 HAR에는 자막 응답이 없습니다. 검색 설정만 만듭니다.\n"
                  "   자막까지 쓰려면 방송 상세의 자막 탭을 연 상태로 HAR을 한 번 더 저장하세요.")
            url, headers = search_pick.url, search_pick.headers

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

    # HAR에는 응답 본문이 들어 있으므로, 네트워크 요청 없이 바로 목록/자막을 뽑을 수 있다.
    if args.extract_refs:
        if not search_pick:
            print("--extract-refs 를 쓰려면 HAR에 검색 응답이 있어야 합니다.", file=sys.stderr)
            return 2
        refs = dc.dedupe(dt.extract_refs(search_pick))
        print(f"\n[목록 추출] 방송 {len(refs)}건")
        for r in refs[:10]:
            print(f"         {r.start_datetime[:16]:16s} {_pad(r.display_channel, 14)} {r.product_name[:40]}")
        if len(refs) > 10:
            print(f"         ... (총 {len(refs)}건)")
        _ensure_parent(args.extract_refs)
        dc.save_refs(args.extract_refs, refs)
        print(f"         저장: {args.extract_refs}")

    if args.extract:
        if not args.har:
            print("--extract 는 --har 과 함께 써야 합니다 (cURL에는 응답 본문이 없습니다).", file=sys.stderr)
            return 2
        if candidate is None:
            print("--extract 를 쓰려면 HAR에 자막 응답이 있어야 합니다.", file=sys.stderr)
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

    cfg = dt.build_config(url, headers or {}, candidate, product_key=args.product_key,
                          body=body, search_hit=search_pick)
    _ensure_parent(args.out_config)
    if os.path.exists(args.out_config) and not args.force:
        print(f"\n[중단] {args.out_config} 가 이미 있습니다. 덮어쓰려면 --force 를 주세요.", file=sys.stderr)
        return 1
    with open(args.out_config, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    made = " + ".join(sorted(cfg["endpoints"]))
    print(f"설정 생성: {args.out_config}  (엔드포인트: {made})")
    print("\n다음 단계:")
    print(f"  1) {args.out_config} 의 endpoints/mapping 을 눈으로 검토")
    print('  2) export HSMOA_DATAHUB_COOKIE="$(cat .secrets/datahub.cookie)"')
    if "search" in cfg["endpoints"]:
        print('  3) python3 -m hsbot collect "로보락" --since 2026-08-01 --html out/report.html')
    else:
        print("  3) python3 -m hsbot fetch --targets config/targets.json --out data/broadcasts.json")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hsbot", description="홈쇼핑 방송 화법 비교 분석기")
    sub = p.add_subparsers(dest="cmd", required=True)

    u = sub.add_parser("url", help="DataHub URL에서 수집 파라미터 추출")
    u.add_argument("url")
    u.set_defaults(func=cmd_url)

    def add_discovery_opts(q, *, default_pages: int) -> None:
        """search / collect 가 공유하는 탐색·선별 옵션."""
        q.add_argument("keyword", help='검색어 (예: "로보락")')
        q.add_argument("--har", default=None,
                       help="검색 결과가 담긴 HAR. 주면 네트워크 없이 이 파일에서 목록을 읽는다")
        q.add_argument("--config", default=None, help="API 설정 (기본 config/datahub.json)")
        q.add_argument("--pages", type=int, default=default_pages, help="검색 페이지 수")
        q.add_argument("--size", type=int, default=50, help="페이지당 개수")
        q.add_argument("--no-cache", action="store_true")
        q.add_argument("--min-score", type=float, default=5.0, help="--har 사용 시 목록 판정 임계값")
        q.add_argument("--pick", type=int, default=0, help="--har 사용 시 쓸 후보 번호")
        # 선별
        q.add_argument("--require", nargs="+", default=[], metavar="KW",
                       help="상품명에 반드시 포함 (예: --require S9 Ultra)")
        q.add_argument("--exclude", nargs="+", default=[], metavar="KW", help="상품명에 있으면 제외")
        q.add_argument("--channel", nargs="+", default=[], metavar="CH", help="이 채널만 (코드/이름 부분일치)")
        q.add_argument("--since", default=None, help="이 시각 이후 방송만 (예: 2026-08-01)")
        q.add_argument("--until", default=None, help="이 시각 이전 방송만")
        q.add_argument("--min-minutes", type=float, default=None, help="편성 길이 하한(분)")
        q.add_argument("--limit", type=int, default=None, help="최대 방송 수")
        q.add_argument("--min-channels", type=int, default=2, help="비교 성립에 필요한 최소 채널 수")
        q.add_argument("--require-keyword", action="store_true",
                       help="상품명에 검색어가 실제로 들어간 것만 (엉뚱한 상품 걸러내기)")

    q = sub.add_parser("search", help='키워드로 방송 목록 탐색 (예: search "로보락")')
    add_discovery_opts(q, default_pages=1)
    q.add_argument("--lexicon", nargs="+", default=["core_ko", "product_robot_vacuum"])
    q.add_argument("--show", type=int, default=30, help="목록을 몇 건까지 출력할지")
    q.add_argument("--out-targets", default=None, metavar="targets.json",
                   help="수집 대상 파일로 저장 (fetch 가 읽는 형식)")
    q.add_argument("--out-refs", default=None, metavar="refs.json", help="검색 결과 원본 목록 저장")
    q.set_defaults(func=cmd_search)

    c = sub.add_parser("collect", help='검색 → 자막 수집 → 비교 분석을 한 번에')
    add_discovery_opts(c, default_pages=3)
    c.add_argument("--model", default=None, help="비교할 모델을 직접 지정 (예: --model S9)")
    c.add_argument("--all-models", action="store_true",
                   help="모델을 가리지 않고 전부 (제품 차이가 섞이므로 권장하지 않음)")
    c.add_argument("--out", default="data/collected.json")
    c.add_argument("--strict", action="store_true", help="한 건이라도 실패하면 중단")
    c.add_argument("--no-analyze", action="store_true", help="수집만 하고 분석은 생략")
    c.add_argument("--lexicon", nargs="+", default=["core_ko", "product_robot_vacuum"])
    c.add_argument("--bucket-sec", type=int, default=300)
    c.add_argument("--spread-threshold", type=float, default=1.35)
    c.add_argument("--html", default="out/report.html")
    c.add_argument("--json", default=None)
    c.add_argument("--title", default=None)
    c.set_defaults(func=cmd_collect)

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
    d.add_argument("--min-score", type=float, default=3.0, help="자막 응답 판정 임계값")
    d.add_argument("--search-min-score", type=float, default=5.0, help="검색 응답 판정 임계값")
    d.add_argument("--limit", type=int, default=5, help="후보를 몇 개까지 출력할지")
    d.add_argument("--pick", type=int, default=0, help="설정 생성에 쓸 자막 후보 번호")
    d.add_argument("--search-pick", type=int, default=0, help="설정 생성에 쓸 검색 후보 번호")
    d.add_argument("--force", action="store_true", help="기존 설정 덮어쓰기")
    d.add_argument("--extract", default=None, metavar="OUT.json",
                   help="HAR 본문에서 자막을 바로 추출해 정규화 JSON으로 저장 (네트워크 불필요)")
    d.add_argument("--extract-refs", default=None, metavar="REFS.json",
                   help="HAR 본문에서 방송 목록을 바로 추출해 저장 (네트워크 불필요)")
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
