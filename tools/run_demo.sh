#!/usr/bin/env bash
# 합성 데이터로 파이프라인 전체를 한 번 돌린다 (API 키 불필요).
set -euo pipefail
cd "$(dirname "$0")/.."

echo "════════ 1단계. 검색으로 방송 찾기 (F12 HAR에서, 네트워크 불필요) ════════"
python3 tools/make_search_har.py
python3 -m hsbot search "로보락" \
  --har fixtures/synthetic/search.har \
  --require-keyword \
  --out-targets out/targets.demo.json

echo
echo "════════ 2단계. 자막 수집 → 비교 분석 ════════"
echo "  ※ 실제로는 위에서 찾은 방송의 자막을 API로 받아오지만(쿠키 필요),"
echo "     이 데모는 네트워크 없이 돌아야 하므로 합성 자막으로 대체한다."
echo
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
