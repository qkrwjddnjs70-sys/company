"""키워드 3개년 추이 + 급상승 키워드 대시보드, 웹 UI.

hsbot/webapp.py와 같은 원칙: 외부 의존성 0 (Flask 등 없이 stdlib `http.server`만
사용), 다크 테마 단일 페이지, 차트는 서버가 그리지 않고 브라우저에서 순수 JS로
SVG를 그린다(별도 차트 라이브러리 CDN도 쓰지 않는다).

사용법
    export TRENDBOT_NAVER_CLIENT_ID=...
    export TRENDBOT_NAVER_CLIENT_SECRET=...
    python3 -m trendbot webapp
    → http://127.0.0.1:8766 접속
"""
from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.parse
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .envfile import load_dotenv
from .naver_api import NaverApiError, NaverDataLabClient, date_n_weeks_ago, date_n_years_ago
from .pool import PoolConfig, PoolConfigError
from .spike import rank_spikes
from .yearly import yearly_overlay

load_dotenv()

MAX_KEYWORDS_PER_REQUEST = 5  # 데이터랩 API 호출 1회 한도(그룹=키워드 1개 고정)


def get_trend(client: NaverDataLabClient, keywords: list[str], *, years: int) -> dict:
    keywords = keywords[:MAX_KEYWORDS_PER_REQUEST]
    start = date_n_years_ago(years).isoformat()
    end = date.today().isoformat()
    series_map = client.search_trend(keywords, start_date=start, end_date=end, time_unit="month")
    overlays = {kw: yearly_overlay(series_map[kw]) for kw in keywords if kw in series_map}
    missing = [kw for kw in keywords if kw not in series_map]
    return {"start": start, "end": end, "overlays": overlays, "missing": missing}


def get_spikes(client: NaverDataLabClient, pool: PoolConfig) -> dict:
    keywords = pool.all_keywords()
    if not keywords:
        return {"results": [], "pool_size": 0,
                "error": "등록된 후보 키워드가 없습니다. config/trendbot.json 을 채우세요."}
    weeks_needed = pool.spike["recent_weeks"] + pool.spike["baseline_weeks"] + 1
    start = date_n_weeks_ago(weeks_needed).isoformat()
    end = date.today().isoformat()
    series_map = client.search_trend(keywords, start_date=start, end_date=end, time_unit="week")
    keyword_ratios = {kw: s.ratios() for kw, s in series_map.items()}
    results = rank_spikes(
        keyword_ratios,
        recent_weeks=pool.spike["recent_weeks"],
        baseline_weeks=pool.spike["baseline_weeks"],
        min_growth_pct=pool.spike["min_growth_pct"],
        min_ratio_floor=pool.spike["min_ratio_floor"],
        keyword_labels=pool.keyword_labels(),
    )
    return {
        "pool_size": len(keywords),
        "params": pool.spike,
        "results": [
            {
                "keyword": r.keyword, "baseline_avg": round(r.baseline_avg, 2),
                "recent_avg": round(r.recent_avg, 2),
                "growth_pct": round(r.growth_pct, 1) if r.growth_pct is not None else None,
                "is_new": r.is_new, "labels": r.labels,
                "sparkline": keyword_ratios.get(r.keyword, []),
            }
            for r in results
        ],
    }


INDEX_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>네이버 검색 트렌드 — 소싱 인사이트</title>
<style>
:root{--bg:#0e1217;--panel:#161b22;--line:#2a323c;--tx:#e6edf3;--tx2:#a8b3bf;--accent:#5aa9ff;
      --y-old:#64748b;--y-mid:#5aa9ff;--y-new:#ffcf5c;--up:#ff8a5c;--new:#c792ea}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font-family:'Noto Sans KR',sans-serif;padding:24px}
.wrap{max-width:920px;margin:0 auto}
h1{font-size:20px;margin-bottom:4px}
.sub{color:var(--tx2);font-size:13px;margin:4px 0 16px}
.tabs{display:flex;gap:8px;margin-bottom:16px}
.tab{background:var(--panel);border:1px solid var(--line);color:var(--tx2);padding:9px 16px;
     border-radius:8px;cursor:pointer;font-size:14px}
.tab.active{color:var(--tx);border-color:var(--accent);background:#1b2330}
.view{display:none}
.view.active{display:block}
input{background:var(--panel);border:1px solid var(--line);color:var(--tx);padding:10px 12px;
      border-radius:8px;font-size:15px;width:280px}
button.act{background:var(--accent);color:#08131f;border:0;padding:10px 18px;border-radius:8px;
           font-size:15px;cursor:pointer;margin-left:8px}
button.act:disabled{background:var(--line);color:var(--tx2);cursor:not-allowed}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;margin:10px 0}
#status,#spStatus{color:var(--tx2);margin:8px 0;font-size:13px}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{padding:9px 8px;border-bottom:1px solid var(--line);text-align:left}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600}
.badge.up{background:rgba(255,138,92,.16);color:var(--up)}
.badge.new{background:rgba(199,146,234,.18);color:var(--new)}
.tag{display:inline-block;background:#1b2330;border:1px solid var(--line);color:var(--tx2);
     border-radius:6px;padding:1px 7px;font-size:11px;margin:0 4px 4px 0}
.legend{display:flex;gap:16px;margin-top:8px;font-size:13px;color:var(--tx2)}
.dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:middle}
</style></head>
<body><div class="wrap">
<h1>네이버 검색 트렌드 — 소싱 인사이트</h1>
<p class="sub">고객이 무언가 필요할 때 가장 먼저 하는 행동, 네이버 검색의 추이를 본다.</p>
<div class="tabs">
  <div class="tab active" data-view="trendView">① 키워드 추이 조회</div>
  <div class="tab" data-view="spikeView">② 급상승 키워드 발견</div>
</div>

<div id="trendView" class="view active">
  <p class="sub">키워드를 입력하면 최근 3개년의 월별 검색 비율을 연도별로 겹쳐 보여준다
  (연도 사이 계절성/성장 여부를 한눈에 비교).</p>
  <form id="tf"><input id="kw" placeholder="예: 로봇청소기" required>
  <button class="act" type="submit">조회</button></form>
  <div id="status"></div>
  <div id="chartOut"></div>
</div>

<div id="spikeView" class="view">
  <p class="sub">config/trendbot.json에 등록해 둔 후보 키워드 중, 최근 검색량이 평소보다
  튄 것을 순위로 보여준다. 네이버가 실시간 급상승 검색어 API를 제공하지 않아,
  후보군은 이 설정 파일 안에서만 찾을 수 있다는 점을 참고.</p>
  <button class="act" id="loadSpikes">급상승 키워드 불러오기</button>
  <div id="spStatus"></div>
  <div id="spikeOut"></div>
</div>

</div>
<script>
document.querySelectorAll('.tab').forEach(t => t.addEventListener('click', () => {
  document.querySelectorAll('.tab').forEach(x => x.classList.remove('active'));
  document.querySelectorAll('.view').forEach(x => x.classList.remove('active'));
  t.classList.add('active');
  document.getElementById(t.dataset.view).classList.add('active');
}));

const YEAR_COLORS = ['#64748b', '#5aa9ff', '#ffcf5c', '#c792ea', '#7ee787'];

function drawYearlyChart(overlay) {
  const years = Object.keys(overlay.years).sort();
  const W = 820, H = 300, padL = 40, padR = 16, padT = 16, padB = 28;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  let maxV = 1;
  years.forEach(y => overlay.years[y].forEach(v => { if (v !== null && v > maxV) maxV = v; }));
  const x = m => padL + (m - 1) / 11 * plotW;
  const y = v => padT + plotH - (v / maxV) * plotH;

  let grid = '';
  for (let i = 0; i <= 4; i++) {
    const yy = padT + plotH - i / 4 * plotH;
    grid += `<line x1="${padL}" y1="${yy}" x2="${W-padR}" y2="${yy}" stroke="#2a323c" stroke-width="1"/>`;
    grid += `<text x="4" y="${yy+4}" font-size="11" fill="#a8b3bf">${Math.round(maxV*i/4)}</text>`;
  }
  const monthLabels = ['1월','2월','3월','4월','5월','6월','7월','8월','9월','10월','11월','12월'];
  monthLabels.forEach((m, i) => {
    grid += `<text x="${x(i+1)}" y="${H-8}" font-size="11" fill="#a8b3bf" text-anchor="middle">${m}</text>`;
  });

  let lines = '', legend = '';
  years.forEach((yr, i) => {
    const color = YEAR_COLORS[i % YEAR_COLORS.length];
    const pts = overlay.years[yr];
    let path = '', started = false;
    pts.forEach((v, mi) => {
      if (v === null) { started = false; return; }
      const cmd = started ? 'L' : 'M';
      path += `${cmd}${x(mi+1).toFixed(1)},${y(v).toFixed(1)} `;
      started = true;
    });
    lines += `<path d="${path}" fill="none" stroke="${color}" stroke-width="2.5"/>`;
    pts.forEach((v, mi) => {
      if (v === null) return;
      lines += `<circle cx="${x(mi+1).toFixed(1)}" cy="${y(v).toFixed(1)}" r="3" fill="${color}">`
             + `<title>${yr}년 ${mi+1}월: ${v.toFixed(1)}</title></circle>`;
    });
    legend += `<span><span class="dot" style="background:${color}"></span>${yr}년</span>`;
  });

  return `<div class="panel">
    <svg width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">${grid}${lines}</svg>
    <div class="legend">${legend}</div>
  </div>`;
}

const tf = document.getElementById('tf');
const status = document.getElementById('status');
const chartOut = document.getElementById('chartOut');
tf.addEventListener('submit', async (e) => {
  e.preventDefault();
  const kw = document.getElementById('kw').value.trim();
  if (!kw) return;
  status.textContent = '조회 중...';
  chartOut.innerHTML = '';
  try {
    const res = await fetch('/api/trend?keyword=' + encodeURIComponent(kw));
    const data = await res.json();
    if (!res.ok) { status.textContent = '오류: ' + data.error; return; }
    const overlay = data.overlays[kw];
    if (!overlay || !Object.keys(overlay.years).length) {
      status.textContent = "'" + kw + "' 결과 없음(관측치 부족 또는 검색량 0)"; return;
    }
    status.textContent = kw + ' — ' + data.start + ' ~ ' + data.end;
    chartOut.innerHTML = drawYearlyChart(overlay);
  } catch (err) { status.textContent = '오류: ' + err; }
});

const spStatus = document.getElementById('spStatus');
const spikeOut = document.getElementById('spikeOut');
document.getElementById('loadSpikes').addEventListener('click', async () => {
  spStatus.textContent = '후보 키워드 조회 중... (키워드 수에 따라 시간이 걸릴 수 있음)';
  spikeOut.innerHTML = '';
  try {
    const res = await fetch('/api/spikes');
    const data = await res.json();
    if (!res.ok || data.error) { spStatus.textContent = '오류: ' + (data.error || res.status); return; }
    spStatus.textContent = `후보 ${data.pool_size}개 중 급상승 ${data.results.length}건 `
      + `(최근 ${data.params.recent_weeks}주 vs 직전 ${data.params.baseline_weeks}주, `
      + `기준 증가율 +${data.params.min_growth_pct}% 이상)`;
    if (!data.results.length) { spikeOut.innerHTML = ''; return; }
    const rows = data.results.map(r => {
      const badge = r.is_new
        ? '<span class="badge new">신규 급증</span>'
        : `<span class="badge up">+${r.growth_pct}%</span>`;
      const tags = r.labels.map(l => `<span class="tag">${l}</span>`).join('');
      return `<tr><td>${r.keyword}${tags ? '<br>'+tags : ''}</td><td>${badge}</td>
        <td>${r.recent_avg}</td><td>${r.baseline_avg}</td></tr>`;
    }).join('');
    spikeOut.innerHTML = `<div class="panel"><table>
      <thead><tr><th>키워드</th><th>변화</th><th>최근 평균</th><th>이전 평균</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  } catch (err) { spStatus.textContent = '오류: ' + err; }
});
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    client: NaverDataLabClient  # set by run()
    pool: PoolConfig | None  # set by run() — 없으면 /api/spikes가 안내 메시지를 준다

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        try:
            if parsed.path == "/":
                self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif parsed.path == "/api/trend":
                keyword = qs.get("keyword", "").strip()
                if not keyword:
                    self._send_json({"error": "keyword required"}, 400)
                    return
                years = int(qs.get("years", "3"))
                self._send_json(get_trend(self.client, [keyword], years=years))
            elif parsed.path == "/api/spikes":
                if self.pool is None:
                    self._send_json({"error": "config/trendbot.json 이 없습니다. "
                                               "config/trendbot.example.json 을 복사해 채우세요.",
                                      "results": [], "pool_size": 0}, 200)
                    return
                self._send_json(get_spikes(self.client, self.pool))
            else:
                self._send(404, b"not found", "text/plain")
        except NaverApiError as e:
            self._send_json({"error": str(e)}, 502)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._send_json({"error": str(e)}, 500)

    def log_message(self, fmt: str, *args) -> None:  # 조용히
        sys.stderr.write("[trendbot webapp] " + (fmt % args) + "\n")


def run(host: str = "127.0.0.1", port: int = 8766) -> None:
    client = NaverDataLabClient()
    Handler.client = client
    try:
        Handler.pool = PoolConfig.load()
    except PoolConfigError:
        Handler.pool = None
        print("[안내] config/trendbot.json 이 없어 '급상승 키워드' 탭은 안내 메시지만 표시합니다.\n"
              "       config/trendbot.example.json 을 복사해 채우면 바로 동작합니다.", file=sys.stderr)
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"trendbot webapp: http://{host}:{port}  (Ctrl+C로 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run(port=int(os.environ.get("PORT", "8766")))
