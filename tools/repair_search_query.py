#!/usr/bin/env python3
"""이미 있는 config/datahub.json 의 `endpoints.search.query` 만 HAR로 다시 맞춘다.

왜 이 도구가 따로 필요한가
--------------------------
`hsbot devtools --har ... --force` 는 설정 **전체**를 다시 만든다. 그러면 나중에
손으로 넣은 것들(`endpoints.broadcast_list`, 자막의 `"method": "POST"` 등)이
사라진다. 이 스크립트는 검색 쿼리 블록 하나만 교체하고 나머지는 손대지 않는다.

무엇을 고치는가
---------------
초기 템플릿화 규칙에 결함이 두 개 있었다.

  · 이름에 'search' 가 들어간다는 이유로 `is_timeline_search` 같은 **불리언 플래그**를
    검색어 자리로 오인해 `is_timeline_search=로보락` 을 보내고 있었다.
  · `offset`(건너뛸 개수)을 `{page}`(쪽 번호)로 취급해, 2페이지를 요청해도
    2건만 건너뛴 거의 같은 목록을 받고 있었다.

기본은 **미리보기**다. 실제로 파일을 고치려면 --write 를 준다.
HAR은 읽기만 하고 어디에도 보내지 않는다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hsbot.sources import devtools as dt   # noqa: E402


def main() -> int:
    p = argparse.ArgumentParser(
        prog="repair_search_query",
        description="config/datahub.json 의 검색 쿼리를 HAR로 다시 맞춘다 (다른 설정은 보존)",
    )
    p.add_argument("--har", required=True, help="검색을 실행한 상태로 저장한 HAR")
    p.add_argument("--config", default=os.path.join("config", "datahub.json"))
    p.add_argument("--pick", type=int, default=0, help="검색 후보가 여러 개일 때 고를 번호")
    p.add_argument("--min-score", type=float, default=5.0)
    p.add_argument("--write", action="store_true", help="미리보기 대신 실제로 저장")
    args = p.parse_args()

    if not os.path.exists(args.config):
        print(f"설정 파일이 없습니다: {args.config}", file=sys.stderr)
        return 2

    hits = dt.scan_har_search(args.har, min_score=args.min_score)
    if not hits:
        print(
            "HAR에서 검색 응답을 찾지 못했습니다.\n"
            "  · 브라우저에서 실제로 키워드 검색을 한 뒤 저장한 HAR인지 확인하세요.\n"
            "  · 임계값을 낮추려면 --min-score 3 을 주세요.",
            file=sys.stderr,
        )
        return 1
    if len(hits) > 1:
        print(f"검색 후보 {len(hits)}건 — [{args.pick}]번을 씁니다.")
        for i, h in enumerate(hits):
            print(f"  [{i}] {h.url[:140]}")
    hit = hits[min(args.pick, len(hits) - 1)]

    with open(args.config, encoding="utf-8") as f:
        cfg = json.load(f)

    sec = dt.build_search_section(hit)
    observed = sec["endpoint"]["query"]
    new_path = sec["endpoint"]["path"]
    old_ep = (cfg.get("endpoints") or {}).get("search") or {}
    old_q = old_ep.get("query") or {}

    # 관측된 키만 값을 교체하고, 이번 요청에 없던 키는 지우지 않고 남긴다.
    # 예를 들어 기간 필터 없이 검색하면 start_date/end_date 가 URL에 안 잡히는데,
    # 그걸 삭제해 버리면 --since/--until 이 조용히 동작하지 않게 된다.
    merged = dict(old_q)
    merged.update(observed)
    kept_only = [k for k in old_q if k not in observed]

    print(f"\n관측된 검색어: {sec['observed_keyword']!r}")
    print(f"경로: {old_ep.get('path', '(없음)')}"
          + ("" if old_ep.get("path") == new_path else f"  →  {new_path}"))

    keys = sorted(merged)
    changed = [k for k in keys if old_q.get(k) != merged.get(k)]
    print(f"\n쿼리 파라미터 {len(keys)}개 중 {len(changed)}개 변경\n")
    width = max((len(k) for k in keys), default=10)
    for k in keys:
        before, after = old_q.get(k, "(없음)"), merged[k]
        if before == after:
            mark, line = ("·" if k in kept_only else " "), f"{after}"
        else:
            mark, line = "*", f"{before}   →   {after}"
        print(f" {mark} {k:<{width}}  {line}")

    if kept_only:
        print(f"\n · 표시 {len(kept_only)}개는 이번 요청에 없던 파라미터입니다: {', '.join(kept_only)}")
        print("   지우지 않고 그대로 두었습니다. 기간 필터처럼 특정 검색에서만 붙는 것일 수 있습니다.")

    new_q = merged

    if not changed and old_ep.get("path") == new_path:
        print("\n바뀔 것이 없습니다. 이미 맞게 들어가 있어요.")
        return 0

    if not args.write:
        print("\n미리보기입니다. 실제로 고치려면 --write 를 붙여 다시 실행하세요.")
        return 0

    cfg.setdefault("endpoints", {}).setdefault("search", {})
    cfg["endpoints"]["search"]["path"] = new_path
    cfg["endpoints"]["search"]["query"] = new_q
    with open(args.config, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n저장했습니다: {args.config}  (검색 쿼리만 교체, 나머지 설정은 그대로)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
