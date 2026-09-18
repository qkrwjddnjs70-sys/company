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
from .yearly import weekly_overlay, yearly_overlay

load_dotenv()

MAX_KEYWORDS_PER_REQUEST = 5  # 데이터랩 API 호출 1회 한도(그룹=키워드 1개 고정)
VALID_UNITS = {"month": yearly_overlay, "week": weekly_overlay}


def get_trend(client: NaverDataLabClient, keywords: list[str], *, years: int, unit: str = "month") -> dict:
    if unit not in VALID_UNITS:
        raise ValueError(f"unit은 {sorted(VALID_UNITS)} 중 하나여야 합니다: {unit!r}")
    overlay_fn = VALID_UNITS[unit]
    keywords = keywords[:MAX_KEYWORDS_PER_REQUEST]
    start = date_n_years_ago(years).isoformat()
    end = date.today().isoformat()
    series_map = client.search_trend(keywords, start_date=start, end_date=end, time_unit=unit)
    overlays = {kw: overlay_fn(series_map[kw]) for kw in keywords if kw in series_map}
    missing = [kw for kw in keywords if kw not in series_map]
    return {"start": start, "end": end, "unit": unit, "overlays": overlays, "missing": missing}


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
.unitToggle{display:inline-flex;gap:6px;margin-left:12px;vertical-align:middle}
.unitBtn{background:var(--panel);border:1px solid var(--line);color:var(--tx2);padding:9px 14px;
         border-radius:8px;font-size:14px;cursor:pointer}
.unitBtn.active{color:var(--tx);border-color:var(--accent);background:#1b2330}
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
  <p class="sub">키워드를 입력하면 최근 3개년의 검색 비율을 연도별로 겹쳐 보여준다
  (연도 사이 계절성/성장 여부를 한눈에 비교). 월간/주간 중 골라서 볼 수 있다.</p>
  <form id="tf">
    <input id="kw" placeholder="예: 로봇청소기" required>
    <button class="act" type="submit">조회</button>
    <span class="unitToggle">
      <button type="button" class="unitBtn active" data-unit="month">월간</button>
      <button type="button" class="unitBtn" data-unit="week">주간</button>
    </span>
  </form>
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

// ISO 주차(1~53) → 그 주의 월요일 날짜. "n월 m주차" 라벨을 만들기 위한 변환.
function isoWeekMonday(year, week) {
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const jan4Dow = jan4.getUTCDay() || 7; // 월=1 ... 일=7
  const week1Monday = new Date(jan4);
  week1Monday.setUTCDate(jan4.getUTCDate() - jan4Dow + 1);
  const target = new Date(week1Monday);
  target.setUTCDate(week1Monday.getUTCDate() + (week - 1) * 7);
  return target;
}

// "1월 2주차"처럼, 그 주가 속한 달과 달 안에서 몇 번째 주인지로 표시한다.
function monthWeekLabel(year, week) {
  const d = isoWeekMonday(Number(year), week);
  const month = d.getUTCMonth() + 1;
  const weekOfMonth = Math.ceil(d.getUTCDate() / 7);
  return `${month}월 ${weekOfMonth}주차`;
}

function drawOverlayChart(overlay) {
  const years = Object.keys(overlay.years).sort();
  const periods = overlay.periods;
  const isWeek = overlay.unit === 'week';
  const W = 820, H = 300, padL = 40, padR = 16, padT = 16, padB = 28;
  const plotW = W - padL - padR, plotH = H - padT - padB;
  const n = periods.length;
  let maxV = 1;
  years.forEach(y => overlay.years[y].forEach(v => { if (v !== null && v > maxV) maxV = v; }));
  const x = idx => padL + idx / (n - 1) * plotW;
  const y = v => padT + plotH - (v / maxV) * plotH;

  let grid = '';
  for (let i = 0; i <= 4; i++) {
    const yy = padT + plotH - i / 4 * plotH;
    grid += `<line x1="${padL}" y1="${yy}" x2="${W-padR}" y2="${yy}" stroke="#2a323c" stroke-width="1"/>`;
    grid += `<text x="4" y="${yy+4}" font-size="11" fill="#a8b3bf">${Math.round(maxV*i/4)}</text>`;
  }
  const axisRefYear = years[years.length - 1]; // 축 라벨은 최근 연도 달력 기준으로 통일해서 매긴다
  periods.forEach((p, i) => {
    // 주간은 53개인 데다 "n월 m주차"라 글자도 길어서, 5주 간격으로만 표시한다.
    if (isWeek && i % 5 !== 0) return;
    const label = isWeek ? monthWeekLabel(axisRefYear, p) : (p + '월');
    grid += `<text x="${x(i)}" y="${H-8}" font-size="11" fill="#a8b3bf" text-anchor="middle">${label}</text>`;
  });

  let lines = '', legend = '';
  years.forEach((yr, i) => {
    const color = YEAR_COLORS[i % YEAR_COLORS.length];
    const pts = overlay.years[yr];
    let path = '', started = false;
    pts.forEach((v, pi) => {
      if (v === null) { started = false; return; }
      const cmd = started ? 'L' : 'M';
      path += `${cmd}${x(pi).toFixed(1)},${y(v).toFixed(1)} `;
      started = true;
    });
    lines += `<path d="${path}" fill="none" stroke="${color}" stroke-width="2.5"/>`;
    pts.forEach((v, pi) => {
      if (v === null) return;
      const label = isWeek ? monthWeekLabel(yr, periods[pi]) : `${periods[pi]}월`;
      lines += `<circle cx="${x(pi).toFixed(1)}" cy="${y(v).toFixed(1)}" r="${isWeek ? 2 : 3}" fill="${color}">`
             + `<title>${yr}년 ${label}: ${v.toFixed(1)}</title></circle>`;
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
let currentUnit = 'month';

document.querySelectorAll('.unitBtn').forEach(btn => btn.addEventListener('click', () => {
  if (btn.dataset.unit === currentUnit) return;
  document.querySelectorAll('.unitBtn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  currentUnit = btn.dataset.unit;
  const kw = document.getElementById('kw').value.trim();
  if (kw) loadTrend(kw);
}));

async function loadTrend(kw) {
  status.textContent = '조회 중...';
  chartOut.innerHTML = '';
  try {
    const res = await fetch('/api/trend?keyword=' + encodeURIComponent(kw) + '&unit=' + currentUnit);
    const data = await res.json();
    if (!res.ok) { status.textContent = '오류: ' + data.error; return; }
    const overlay = data.overlays[kw];
    if (!overlay || !Object.keys(overlay.years).length) {
      status.textContent = "'" + kw + "' 결과 없음(관측치 부족 또는 검색량 0)"; return;
    }
    const unitLabel = currentUnit === 'week' ? '주간' : '월간';
    status.textContent = kw + ' — ' + unitLabel + ' — ' + data.start + ' ~ ' + data.end;
    chartOut.innerHTML = drawOverlayChart(overlay);
  } catch (err) { status.textContent = '오류: ' + err; }
}

tf.addEventListener('submit', async (e) => {
  e.preventDefault();
  const kw = document.getElementById('kw').value.trim();
  if (!kw) return;
  await loadTrend(kw);
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
                unit = qs.get("unit", "month")
                if unit not in VALID_UNITS:
                    self._send_json({"error": f"unit은 {sorted(VALID_UNITS)} 중 하나여야 합니다"}, 400)
                    return
                self._send_json(get_trend(self.client, [keyword], years=years, unit=unit))
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
