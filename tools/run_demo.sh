#!/usr/bin/env bash
# 합성 데이터로 파이프라인 전체를 한 번 돌린다 (API 키 불필요).
set -euo pipefail
cd "$(dirname "$0")/.."

python3 tools/make_synthetic_fixtures.py

declare -A NAMES=( [gsshop]="GS SHOP" [cjonstyle]="CJ온스타일" [lotte]="롯데홈쇼핑" )
for code in "${!NAMES[@]}"; do
  python3 -m hsbot paste \
    --file "fixtures/synthetic/${code}_robotvac.txt" \
    --channel "$code" --channel-name "${NAMES[$code]}" \
    --product "로보락 S9 MAX Ultra" \
    --start "2026-09-04T20:38:00+09:00" \
    --end   "2026-09-04T21:38:00+09:00" \
    --out "data/${code}.json"
done

python3 -m hsbot analyze \
  --input "data/*.json" \
  --lexicon core_ko product_robot_vacuum \
  --html out/report.html --json out/metrics.json \
  --source-note "합성 검증 데이터 (실제 방송 아님)"
