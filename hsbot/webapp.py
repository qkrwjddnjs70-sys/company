"""키워드 → 채널별 최근 방송일자 → 선택 → 자동 리포트, 웹 UI.

의존성 0 원칙을 지키기 위해 stdlib `http.server`만 쓴다(Flask 등 없음).
실적(추정 매출)은 아직 다루지 않는다 — 소스를 못 찾아 뒤로 미뤘다(README/작업 기록 참고).

사용법
    HSMOA_DATAHUB_COOKIE="..." python -m hsbot webapp
    → http://127.0.0.1:8765 접속
"""
from __future__ import annotations

import html
import json
import os
import sys
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import discover as dc
from .compare import compare as compare_broadcasts
from .lexicon import load_combined
from .metrics import analyze as analyze_one
from .report import render as render_html
from .sources.datahub import DataHubClient, DataHubError

# 검색 응답에 채널명 필드가 없어(mapping.search.channel_name_paths 비움) 코드로 예쁘게 만든다.
CHANNEL_NAMES = {
    "gsshop": "GS샵", "gsmyshop": "GS샵",
    "cjmall": "CJ온스타일",
    "lotteimall": "롯데홈쇼핑", "lotte": "롯데홈쇼핑",
    "hmall": "현대홈쇼핑",
    "ssgshop": "SSG닷컴",
    "nsmall": "NS홈쇼핑",
    "kshop": "K쇼핑",
    "shopping1": "쇼핑1번가", "ohouse": "공영홈쇼핑",
}


def channel_display(code: str, fallback: str | None = None) -> str:
    return fallback or CHANNEL_NAMES.get(code, code)


def default_lexicons(keyword: str) -> list[str]:
    names = ["core_ko"]
    if any(k in keyword for k in ("로보락", "청소기", "vacuum", "로봇")):
        names.append("product_robot_vacuum")
    return names


def search_broadcasts(client: DataHubClient, keyword: str, *, limit: int = 20) -> dict:
    """키워드 → 이 상품을 판 방송을 최신순으로 최대 `limit`개 나열한다(채널 뒤섞임).

    검색 API는 "로보락 S9 MAX ULTRA"처럼 브랜드+모델을 한 문장으로 넣으면
    0건을 준다(정확 문구 일치에 가깝다). 반면 브랜드 한 단어("로보락")로는
    잘 찾는다. 그래서 검색은 브랜드 단어로만 하고, 모델을 좁히는 건
    로컬에서 model_key 매칭으로 처리한다.
    """
    tokens = keyword.split()
    refs = dc.dedupe(client.search(keyword, pages=1, size=50))
    if not refs and tokens:
        refs = dc.dedupe(client.search(tokens[0], pages=1, size=50))
    if not refs:
        return {"keyword": keyword, "model": "", "broadcasts": []}

    extra_tokens = [t.upper() for t in tokens[1:]]
    groups = dc.comparable_groups(refs, min_channels=1)
    if extra_tokens and groups:
        def _score(kv: tuple[str, list[dc.BroadcastRef]]) -> tuple[int, int]:
            model_key, members = kv
            hits = sum(1 for t in extra_tokens if t in model_key)
            return (hits, len({r.channel for r in members}))

        model, group_refs = max(groups, key=_score)
    else:
        best = dc.pick_best_group(refs, min_channels=1)
        model, group_refs = best if best else (refs[0].model_key or keyword, refs)

    by_channel: dict[str, dc.BroadcastRef] = {}
    for r in group_refs:
        prev = by_channel.get(r.channel)
        if prev is None or (r.start_datetime or "") > (prev.start_datetime or ""):
            by_channel[r.channel] = r

    by_channel_broadcasts: dict[str, list[dict]] = {}
    for channel, rep in by_channel.items():
        try:
            history = client.list_broadcasts(rep.product_key, use_cache=True)
        except DataHubError:
            continue
        items = [
            {
                "broadcast_id": h.get("broadcast_id"),
                "channel": channel,
                "channel_name": channel_display(channel, rep.channel_name),
                "product_key": rep.product_key,
                "product_name": rep.product_name,
                "start_datetime": h.get("start_datetime") or "",
                "end_datetime": h.get("end_datetime") or "",
                "duration_min": h.get("duration_min"),
            }
            for h in history
        ]
        items.sort(key=lambda b: b["start_datetime"], reverse=True)
        by_channel_broadcasts[channel] = items

    # 채널별로 번갈아 채워서(라운드로빈), 방송이 많은 한 채널이 목록을 독식해
    # 다른 채널이 밀려나는 일을 막는다 — 3개 이상 채널을 골라 비교하기 쉬워진다.
    broadcasts: list[dict] = []
    queues = [q for q in by_channel_broadcasts.values() if q]
    idx = 0
    while queues and len(broadcasts) < limit:
        queue = queues[idx % len(queues)]
        broadcasts.append(queue.pop(0))
        if not queue:
            queues.pop(idx % len(queues))
        else:
            idx += 1
    broadcasts.sort(key=lambda b: b["start_datetime"], reverse=True)
    return {"keyword": keyword, "model": model, "broadcasts": broadcasts[:limit]}


def fetch_labeled(client: DataHubClient, item: dict):
    bc = client.fetch_by_broadcast_id(
        item["broadcast_id"],
        product_key=item["product_key"],
        channel=item["channel"],
        start_datetime=item["start_datetime"],
        end_datetime=item.get("end_datetime") or item["start_datetime"],
        product_name=item.get("product_name") or None,
        use_cache=True,
    )
    bc.channel_name = channel_display(item["channel"], item.get("channel_name") or bc.channel_name)
    return bc


def build_compare_html(client: DataHubClient, *, items: list[dict], keyword: str) -> str:
    if not items:
        raise DataHubError("비교할 방송을 선택하지 않았습니다.")
    broadcasts = [fetch_labeled(client, it) for it in items]
    product_name = keyword or next((it.get("product_name") for it in items if it.get("product_name")), "")
    lex = load_combined(default_lexicons(keyword or product_name))
    metrics = [analyze_one(bc, lex) for bc in broadcasts]
    res = compare_broadcasts(broadcasts, metrics)
    ids = ",".join(str(it["broadcast_id"]) for it in items)
    return render_html(res, title=f"{product_name} — 홈쇼핑 채널별 판매 화법 비교" if product_name else None,
                       source_note=f"broadcast_id={ids}")


INDEX_HTML = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>홈쇼핑 채널별 화법 비교</title>
<style>
:root{--bg:#0e1217;--panel:#161b22;--line:#2a323c;--tx:#e6edf3;--tx2:#a8b3bf;--accent:#5aa9ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font-family:'Noto Sans KR',sans-serif;padding:24px}
.wrap{max-width:820px;margin:0 auto}
h1{font-size:20px}
input{background:var(--panel);border:1px solid var(--line);color:var(--tx);padding:10px 12px;border-radius:8px;font-size:15px;width:320px}
button{background:var(--accent);color:#08131f;border:0;padding:10px 18px;border-radius:8px;font-size:15px;cursor:pointer;margin-left:8px}
button:disabled{background:var(--line);color:var(--tx2);cursor:not-allowed}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:8px 16px;margin:10px 0}
table{width:100%;border-collapse:collapse;font-size:14px}
td,th{padding:8px 8px;border-bottom:1px solid var(--line);text-align:left}
td:first-child{width:34px;text-align:center}
.sub{color:var(--tx2);font-size:13px}
#status{color:var(--tx2);margin:8px 0}
#bar{display:flex;align-items:center;gap:10px;margin:10px 0}
</style></head>
<body><div class="wrap">
<h1>키워드로 방송 찾아 비교하기</h1>
<p class="sub">실적(추정 매출)은 아직 표시하지 않습니다 — 최근 방송 최대 20개를 나열합니다. 2개 이상 선택하면 비교할 수 있고, 채널이 많을수록(3개 이상) 어느 채널이 무엇을 밀었는지 더 뚜렷하게 갈립니다.</p>
<form id="f"><input id="kw" placeholder="예: 로보락 s9 max ultra" required>
<button type="submit">검색</button></form>
<div id="status"></div>
<div id="bar" style="display:none">
  <span id="selCount" class="sub"></span>
  <button id="cmp" disabled>선택한 방송 비교</button>
</div>
<div id="out"></div>
</div>
<form id="cmpForm" method="POST" action="/api/compare" target="_blank" style="display:none">
  <input type="hidden" name="items" id="cmpItems">
  <input type="hidden" name="keyword" id="cmpKeyword">
</form>
<script>
const f = document.getElementById('f');
const out = document.getElementById('out');
const status = document.getElementById('status');
const bar = document.getElementById('bar');
const cmpBtn = document.getElementById('cmp');
const selCount = document.getElementById('selCount');
let lastItems = [];
let currentKeyword = '';

function updateBar() {
  const n = out.querySelectorAll('input.sel:checked').length;
  selCount.textContent = n + '개 선택';
  cmpBtn.disabled = n < 2;
}

f.addEventListener('submit', async (e) => {
  e.preventDefault();
  const kw = document.getElementById('kw').value.trim();
  if (!kw) return;
  currentKeyword = kw;
  out.innerHTML = '';
  bar.style.display = 'none';
  status.textContent = '검색 중...';
  try {
    const res = await fetch('/api/search?keyword=' + encodeURIComponent(kw));
    const data = await res.json();
    if (!res.ok) { status.textContent = '오류: ' + data.error; return; }
    lastItems = data.broadcasts;
    if (!lastItems.length) { status.textContent = '결과 없음'; return; }
    status.textContent = '모델: ' + data.model + ' · ' + lastItems.length + '건';
    const rows = lastItems.map((b, i) => `<tr>
      <td><input type="checkbox" class="sel" data-i="${i}"></td>
      <td>${(b.start_datetime || '').replace('T',' ')}</td>
      <td>${b.channel_name}</td>
      <td>${b.duration_min ?? ''}분</td>
    </tr>`).join('');
    out.innerHTML = `<div class="panel"><table>
      <thead><tr><th></th><th>방송 시작</th><th>홈쇼핑사</th><th>편성</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
    bar.style.display = '';
    updateBar();
    out.querySelectorAll('input.sel').forEach(cb => cb.addEventListener('change', updateBar));
  } catch (err) {
    status.textContent = '오류: ' + err;
  }
});

cmpBtn.addEventListener('click', () => {
  const picked = [...out.querySelectorAll('input.sel:checked')].map(cb => lastItems[+cb.dataset.i]);
  if (picked.length < 2) return;
  document.getElementById('cmpItems').value = JSON.stringify(picked);
  document.getElementById('cmpKeyword').value = currentKeyword;
  document.getElementById('cmpForm').submit();
});
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    client: DataHubClient  # set by run()

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, obj) -> None:
        self._send(200, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send_error(self, e: Exception, *, as_json: bool) -> None:
        msg = str(e)
        if as_json:
            self._send(500, json.dumps({"error": msg}, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        else:
            self._send(500, f"<pre>{html.escape(msg)}</pre>".encode("utf-8"), "text/html; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        as_json = parsed.path.startswith("/api/")
        try:
            if parsed.path == "/":
                self._send(200, INDEX_HTML.encode("utf-8"), "text/html; charset=utf-8")
            elif parsed.path == "/api/search":
                keyword = qs.get("keyword", "").strip()
                if not keyword:
                    self._send(400, b'{"error":"keyword required"}', "application/json")
                    return
                self._send_json(search_broadcasts(self.client, keyword))
            else:
                self._send(404, b"not found", "text/plain")
        except DataHubError as e:
            self._send_error(e, as_json=as_json)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._send_error(e, as_json=as_json)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        try:
            if parsed.path == "/api/compare":
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length).decode("utf-8")
                form = {k: v[0] for k, v in urllib.parse.parse_qs(body).items()}
                items = json.loads(form.get("items", "[]"))
                html_out = build_compare_html(self.client, items=items, keyword=form.get("keyword", ""))
                self._send(200, html_out.encode("utf-8"), "text/html; charset=utf-8")
            else:
                self._send(404, b"not found", "text/plain")
        except DataHubError as e:
            self._send_error(e, as_json=False)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._send_error(e, as_json=False)

    def log_message(self, fmt: str, *args) -> None:  # 조용히
        sys.stderr.write("[webapp] " + (fmt % args) + "\n")


def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    client = DataHubClient()
    Handler.client = client
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"hsbot webapp: http://{host}:{port}  (Ctrl+C로 종료)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    run(port=int(os.environ.get("PORT", "8765")))
