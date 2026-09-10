"""비교 결과 → 다크모드 HTML 리포트."""
from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any

from .compare import ComparisonResult
from .metrics import BroadcastMetrics

CHANNEL_COLORS = ["#5aa9ff", "#ffb454", "#5fd7a4", "#ff7b9c", "#b48cff", "#f2d06b"]

CSS = """
:root{
  --bg:#0e1217; --panel:#161b22; --panel2:#1b2129; --line:#2a323c;
  --tx:#e6edf3; --tx2:#a8b3bf; --tx3:#6e7c8c;
  --good:#5fd7a4; --warn:#ffb454; --bad:#ff7b9c; --accent:#5aa9ff;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);
  font-family:Pretendard,'Apple SD Gothic Neo','Noto Sans KR',-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
  font-size:15px;line-height:1.65;-webkit-font-smoothing:antialiased}
.wrap{max-width:1180px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:23px;margin:0 0 6px;letter-spacing:-.02em}
h2{font-size:18px;margin:38px 0 12px;padding-left:11px;border-left:3px solid var(--accent);letter-spacing:-.01em}
h3{font-size:15px;margin:20px 0 8px;color:var(--tx2)}
p{margin:8px 0}
.sub{color:var(--tx3);font-size:13.5px}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:12px 0}
.note{background:rgba(255,180,84,.08);border:1px solid rgba(255,180,84,.28);border-radius:10px;padding:12px 14px;font-size:14px;color:#ffd9a3}
.grid{display:grid;gap:12px}
.cards{grid-template-columns:repeat(auto-fit,minmax(280px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px;border-top:3px solid var(--ch,#5aa9ff)}
.card .name{font-weight:700;font-size:16px;margin-bottom:2px}
.kv{display:flex;justify-content:space-between;gap:10px;padding:3px 0;font-size:14px;border-bottom:1px dashed rgba(255,255,255,.05)}
.kv:last-child{border-bottom:0}
.kv span:first-child{color:var(--tx3)}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12.5px;margin:2px 4px 2px 0;
  background:rgba(90,169,255,.13);border:1px solid rgba(90,169,255,.3);color:#bcdcff}
.tag.dn{background:rgba(255,123,156,.12);border-color:rgba(255,123,156,.3);color:#ffc0d0}
.scroll{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{border-collapse:collapse;width:100%;font-size:14px;min-width:520px}
th,td{padding:8px 10px;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--tx3);font-weight:600;font-size:13px;background:var(--panel2);position:sticky;top:0}
td:first-child,th:first-child{text-align:left;white-space:normal}
tbody tr:hover{background:rgba(255,255,255,.03)}
.num{font-variant-numeric:tabular-nums}
.bar{position:relative;height:16px;border-radius:4px;background:rgba(255,255,255,.05);overflow:hidden;min-width:70px}
.bar>i{position:absolute;left:0;top:0;bottom:0;border-radius:4px;display:block}
.lead-cell{background:rgba(255,255,255,.06);border-radius:8px;font-weight:600}
.legend{display:flex;flex-wrap:wrap;gap:12px;font-size:13px;color:var(--tx2);margin:6px 0 2px}
.legend b{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:5px;vertical-align:-1px}
.hm{display:grid;gap:2px;margin:2px 0}
.hm .lbl{font-size:12.5px;color:var(--tx3);text-align:right;padding-right:8px;white-space:nowrap;line-height:20px}
.hm .cell{height:20px;border-radius:3px;background:rgba(255,255,255,.04)}
.hm .hd{font-size:11.5px;color:var(--tx3);text-align:center;line-height:16px}
.up{color:var(--good)} .dn{color:var(--bad)} .mid{color:var(--tx2)}
code{background:var(--panel2);padding:1px 5px;border-radius:4px;font-size:13px;color:#ffd9a3}
ul{margin:6px 0;padding-left:20px} li{margin:3px 0}
footer{margin-top:44px;color:var(--tx3);font-size:13px;border-top:1px solid var(--line);padding-top:14px}
.clickable{cursor:pointer}
.clickable:hover{outline:1px solid rgba(255,255,255,.25)}
#quoteOverlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:50;align-items:center;justify-content:center;padding:20px}
#quoteOverlay.open{display:flex}
#quoteModal{background:var(--panel);border:1px solid var(--line);border-radius:12px;max-width:640px;width:100%;max-height:80vh;overflow-y:auto;padding:18px 20px}
#quoteModal h3{margin:0 0 4px}
#quoteModal .close{float:right;cursor:pointer;color:var(--tx3);font-size:20px;line-height:1}
.qline{padding:8px 0;border-bottom:1px dashed rgba(255,255,255,.08);font-size:14px}
.qline .t{color:var(--tx3);font-size:12.5px;margin-right:8px}
.qline mark{background:rgba(90,169,255,.28);color:inherit;border-radius:3px;padding:0 2px}
"""


def _e(x: Any) -> str:
    return html.escape(str(x))


def _fmt(v: Any, digits: int = 1) -> str:
    if v is None:
        return "<span class='mid'>–</span>"
    if isinstance(v, float):
        return f"{v:,.{digits}f}"
    if isinstance(v, int):
        return f"{v:,}"
    return _e(v)


def _bar(pct: float, color: str) -> str:
    return f"<div class='bar'><i style='width:{max(2.0, min(100.0, pct)):.1f}%;background:{color}'></i></div>"


def _mmss(sec: float | None) -> str:
    if sec is None:
        return "–"
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def _channel_color(i: int) -> str:
    return CHANNEL_COLORS[i % len(CHANNEL_COLORS)]


# ---------------- 섹션 ----------------

def _summary_cards(res: ComparisonResult, colors: dict[str, str]) -> str:
    cards = []
    for m in res.metrics:
        p = res.profile[m.broadcast_id]
        strong = "".join(f"<span class='tag'>{_e(l)} {v:.0f}</span>" for l, v in p["strong_axes"]) or "<span class='sub'>뚜렷한 강점축 없음</span>"
        weak = "".join(f"<span class='tag dn'>{_e(l)} {v:.0f}</span>" for l, v in p["weak_axes"]) or "<span class='sub'>–</span>"
        cards.append(f"""
<div class="card" style="--ch:{colors[m.broadcast_id]}">
  <div class="name">{_e(m.channel_name)}</div>
  <div class="sub">{_e(m.start_datetime[:16].replace('T',' '))} · {m.duration_min:.0f}분</div>
  <div style="margin-top:10px">
    <div class="kv"><span>발화 밀도</span><span class="num">{m.chars_per_min:,.0f}자/분</span></div>
    <div class="kv"><span>자막 줄 수</span><span class="num">{m.n_segments:,}줄 ({m.segments_per_min:.1f}/분)</span></div>
    <div class="kv"><span>반복 지수</span><span class="num">{m.repetition_index*100:.1f}%</span></div>
    <div class="kv"><span>첫 가격 언급</span><span class="num">{_e(p['first_price_at'] or '없음')}</span></div>
    <div class="kv"><span>첫 마감압박</span><span class="num">{_e(p['first_urgency_at'] or '없음')}</span></div>
    <div class="kv"><span>최저가 언급</span><span class="num">{('%s원' % f"{p['lowest_price']:,}") if p['lowest_price'] else '–'}</span></div>
  </div>
  <div style="margin-top:10px"><div class="sub">강하게 민 축</div>{strong}</div>
  <div style="margin-top:6px"><div class="sub">덜 민 축</div>{weak}</div>
</div>""")
    return f"<div class='grid cards'>{''.join(cards)}</div>"


def _volume_table(res: ComparisonResult) -> str:
    rows = []
    for m in res.metrics:
        n = m.numeric
        rows.append(f"""<tr>
<td>{_e(m.channel_name)}</td>
<td class="num">{m.duration_min:.0f}</td>
<td class="num">{m.n_segments:,}</td>
<td class="num">{m.segments_per_min:.1f}</td>
<td class="num">{m.n_chars:,}</td>
<td class="num">{m.chars_per_min:,.0f}</td>
<td class="num">{m.avg_chars_per_segment:.1f}</td>
<td class="num">{m.type_token_ratio*100:.1f}%</td>
<td class="num">{m.repetition_index*100:.1f}%</td>
<td class="num">{n.price_mentions}</td>
<td class="num">{_mmss(n.first_price_sec)}</td>
</tr>""")
    return f"""<div class="scroll"><table>
<thead><tr>
<th>채널</th><th>편성(분)</th><th>자막줄</th><th>줄/분</th><th>총 글자</th><th>자/분</th>
<th>줄당 글자</th><th>어휘 다양도</th><th>반복지수</th><th>가격언급</th><th>첫 가격</th>
</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<p class="sub">· 어휘 다양도(TTR)가 낮고 반복지수가 높을수록 <b>같은 말을 반복하는 화법</b>입니다.
· '자/분'은 편성 길이가 달라도 비교되도록 정규화한 발화 밀도입니다.</p>"""


def _axis_table(res: ComparisonResult, colors: dict[str, str]) -> str:
    ids = res.ids
    name = {m.broadcast_id: m.channel_name for m in res.metrics}
    head = "".join(f"<th style='text-align:center;color:{colors[b]}'>{_e(name[b])}</th>" for b in ids)
    rows = []
    for a in res.axes:
        mx = max(a.values.values()) or 1.0
        leader_name = name.get(a.leader, "–")
        leader_color = colors.get(a.leader, "var(--tx)")
        cells = []
        for b in ids:
            v = a.values.get(b, 0.0)
            is_leader = b == a.leader and v > 0
            crown = " 👑" if is_leader else ""
            cls = "lead-cell" if is_leader else ""
            has_quotes = bool(res.by_id(b).axes.get(a.key) and res.by_id(b).axes[a.key].example_lines)
            click_attrs = f" data-bid='{_e(b)}' data-axis='{_e(a.key)}'" if has_quotes else ""
            cls2 = f"{cls} clickable".strip() if has_quotes else cls
            cells.append(
                f"<td class='num {cls2}' style='min-width:110px'{click_attrs}>{_bar(100 * v / mx, colors[b])}"
                f"<span class='sub num'>{v:.2f}{crown}</span></td>"
            )
        flag = " 🔺" if a.key in res.differentiating_axes else ""
        rows.append(
            f"<tr><td><b style='color:{a.color}'>■</b> {_e(a.label)}{flag}"
            f"<div class='sub'>1위 <b style='color:{leader_color}'>{_e(leader_name)}</b> · 편차 ×{a.spread:.2f}</div>"
            f"</td>{''.join(cells)}</tr>"
        )
    return f"""<div class="scroll"><table>
<thead><tr><th>소구축</th>{head}</tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="sub">· 막대는 <b>분당 등장 횟수</b>이며 범례와 같은 채널 색으로 표시합니다. 👑 = 그 축을 가장 강하게 민 채널.
· 🔺 표시는 채널 간 편차가 큰 축, 즉 <b>판매 전략이 실제로 갈린 지점</b>입니다.
· 축은 상호배타적이지 않습니다. 한 문장이 가격·긴급을 동시에 자극하면 양쪽에 카운트됩니다.
· 셀 테두리가 밝아지는(클릭 가능한) 칸을 누르면 그 채널이 실제로 뭐라고 말했는지(멘트) 볼 수 있습니다.</p>"""


def _numeric_table(res: ComparisonResult) -> str:
    rows = []
    for m in res.metrics:
        n = m.numeric
        prices = ", ".join(f"{p:,}" for p in n.distinct_prices[:6]) or "–"
        rows.append(f"""<tr>
<td>{_e(m.channel_name)}</td>
<td class="num">{n.price_mentions}</td>
<td class="num">{n.price_mentions_per_min:.2f}</td>
<td class="num">{_fmt(n.lowest_price)}</td>
<td class="num">{_fmt(n.highest_price)}</td>
<td class="num">{n.percent_mentions}</td>
<td class="num">{_fmt(n.max_percent)}</td>
<td class="num">{n.installment_mentions}</td>
<td class="num">{_fmt(n.max_installment_months, 0)}</td>
<td class="sub num">{_e(prices)}</td>
</tr>""")
    return f"""<div class="scroll"><table>
<thead><tr><th>채널</th><th>가격언급</th><th>분당</th><th>최저</th><th>최고</th>
<th>%언급</th><th>최대%</th><th>개월언급</th><th>최대개월</th><th>등장 금액(원)</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table></div>
<p class="sub">· 자막에서 정규식으로 추출한 숫자입니다. '39만 8천원' 같은 복합 표기도 합산합니다.
· 실제 판매가가 아니라 <b>방송에서 입으로 말한 숫자</b>라는 점에 유의하세요(정상가·경쟁가·월납 금액이 섞일 수 있음).</p>"""


def _timeline(res: ComparisonResult, colors: dict[str, str], max_axes: int = 8) -> str:
    keys = [a.key for a in res.axes[:max_axes]]
    labels = {a.key: a.label for a in res.axes}
    axcolor = {a.key: a.color for a in res.axes}
    blocks = []
    for m in res.metrics:
        n = len(m.timeline)
        cols = f"110px repeat({n},minmax(30px,1fr))"
        hdr = "".join(f"<div class='hd'>{_e(t['label'])}</div>" for t in m.timeline)
        rows = [f"<div class='lbl'>구간</div>{hdr}"]
        # 발화 밀도 행
        mx_chars = max((t["chars_per_min"] for t in m.timeline), default=1) or 1
        cells = "".join(
            f"<div class='cell' title='{t['chars_per_min']:.0f}자/분' "
            f"style='background:rgba(90,169,255,{0.08 + 0.85 * t['chars_per_min'] / mx_chars:.2f})'></div>"
            for t in m.timeline
        )
        rows.append(f"<div class='lbl'>발화 밀도</div>{cells}")
        for k in keys:
            if k not in m.axes:
                continue
            vals = [t["axes"].get(k, 0.0) for t in m.timeline]
            mx = max(vals) or 1.0
            c = axcolor[k].lstrip("#")
            rgb = ",".join(str(int(c[i:i + 2], 16)) for i in (0, 2, 4))
            cells = "".join(
                f"<div class='cell' title='{labels[k]} {v:.2f}/분' "
                f"style='background:rgba({rgb},{0.06 + 0.9 * v / mx:.2f})'></div>"
                for v in vals
            )
            rows.append(f"<div class='lbl'>{_e(labels[k])}</div>{cells}")
        grid = "".join(f"<div class='hm' style='grid-template-columns:{cols}'>{r}</div>" for r in rows)
        blocks.append(
            f"<div class='panel'><h3 style='color:{colors[m.broadcast_id]};margin-top:0'>"
            f"{_e(m.channel_name)}</h3><div class='scroll'><div style='min-width:560px'>{grid}</div></div></div>"
        )
    return "".join(blocks) + (
        "<p class='sub'>· 각 행은 그 채널 <b>자신의 최대값 기준</b>으로 색이 진해집니다(행 내부 비교용). "
        "채널 간 절대 강도 비교는 위의 소구축 표를 보세요.</p>"
    )


def _distinctive(res: ComparisonResult, colors: dict[str, str]) -> str:
    name = {m.broadcast_id: m.channel_name for m in res.metrics}
    cards = []
    for bid in res.ids:
        terms = res.distinctive.get(bid, [])
        body = "".join(
            f"<div class='kv'><span>{_e(t)}</span><span class='num'>z {z:+.1f} · {c}회</span></div>"
            for t, z, c in terms
        ) or "<div class='sub'>유의미한 차별 표현 없음</div>"
        cards.append(
            f"<div class='card' style='--ch:{colors[bid]}'>"
            f"<div class='name'>{_e(name[bid])}</div>{body}</div>"
        )
    return (
        f"<div class='grid cards'>{''.join(cards)}</div>"
        "<p class='sub'>· 단순 빈도가 아니라 <b>다른 방송 대비 초과 사용량</b>(Dirichlet 사전분포 로그오즈비 z)입니다. "
        "'이거·그래서' 같은 공통어는 자동으로 걸러집니다. z가 클수록 그 채널만의 화법입니다.</p>"
    )


def _quotes_json(res: ComparisonResult) -> str:
    data: dict[str, dict[str, Any]] = {}
    for m in res.metrics:
        axes_out = {}
        for key, am in m.axes.items():
            if am.example_lines:
                axes_out[key] = {
                    "label": am.label,
                    "channel": m.channel_name,
                    "lines": am.example_lines,
                }
        if axes_out:
            data[m.broadcast_id] = axes_out
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")


def render(
    res: ComparisonResult,
    *,
    title: str | None = None,
    source_note: str = "",
) -> str:
    colors = {m.broadcast_id: _channel_color(i) for i, m in enumerate(res.metrics)}
    legend = "".join(
        f"<span><b style='background:{colors[m.broadcast_id]}'></b>{_e(m.channel_name)}</span>"
        for m in res.metrics
    )
    diff_labels = [a.label for a in res.axes if a.key in res.differentiating_axes] or ["없음"]
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    ttl = title or f"{res.product_name} — 홈쇼핑 채널별 판매 화법 비교"
    quotes_json = _quotes_json(res)

    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(ttl)}</title><style>{CSS}</style></head><body><div class="wrap">
<h1>{_e(ttl)}</h1>
<p class="sub">비교 방송 {len(res.metrics)}건 · 생성 {now}{(' · ' + _e(source_note)) if source_note else ''}</p>
<div class="legend">{legend}</div>

<div class="panel">
  <b>한 줄 요약</b>
  <p>같은 상품인데 채널 간 화법이 가장 크게 갈린 축은 <b>{_e(', '.join(diff_labels[:4]))}</b>입니다.</p>
</div>

<h2>1. 채널별 전략 요약</h2>
{_summary_cards(res, colors)}

<h2>2. 발화량 · 화법 밀도</h2>
{_volume_table(res)}

<h2>3. 소구축별 강도 비교</h2>
{_axis_table(res, colors)}

<h2>4. 방송 구성 타임라인</h2>
{_timeline(res, colors)}

<h2>5. 숫자 소구 (가격 · 할인율 · 할부)</h2>
{_numeric_table(res)}

<h2>6. 채널별 차별 표현</h2>
{_distinctive(res, colors)}

<h2>7. 방법론과 한계</h2>
<div class="panel">
<ul>
<li><b>정규화</b> — 편성 길이가 다르면 총량 비교가 왜곡되므로 모든 지표를 분당으로 환산합니다.</li>
<li><b>축 중복 허용</b> — "지금 이 시간 최저가"는 가격·긴급 양쪽에 카운트됩니다. 실제 화법이 복합적이기 때문입니다.</li>
<li><b>키워드 사전 기반</b> — 문맥·반어·부정을 이해하지 못합니다. 예: "비싸지 않습니다"도 가격 축에 잡힙니다.</li>
<li><b>형태소 분석기 미사용</b> — 조사 제거는 규칙 기반 근사입니다. 절대 빈도보다 <b>채널 간 상대 비교</b>에 쓰세요.</li>
<li><b>자막 품질 의존</b> — 자동 음성인식 자막이면 오인식이 그대로 지표에 반영됩니다.</li>
<li><b>인과 해석 금지</b> — 이 리포트는 "어떻게 팔았나"를 재는 것이지 "그래서 잘 팔렸나"를 증명하지 않습니다. 매출 지표와 결합해야 의미가 생깁니다.</li>
</ul>
</div>

<footer>홈쇼핑 비교봇 · hsbot</footer>
</div>

<div id="quoteOverlay">
  <div id="quoteModal">
    <span class="close" id="quoteClose">&times;</span>
    <h3 id="quoteTitle"></h3>
    <div class="sub" id="quoteSub" style="margin-bottom:10px"></div>
    <div id="quoteBody"></div>
  </div>
</div>
<script>
const QUOTES = {quotes_json};
function mmss(sec) {{
  sec = Math.max(0, Math.round(sec || 0));
  return Math.floor(sec/60) + ':' + String(sec%60).padStart(2,'0');
}}
function esc(s) {{
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}}
function highlight(text, terms) {{
  let out = esc(text);
  (terms || []).forEach(t => {{
    if (!t) return;
    const re = new RegExp(esc(t).replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&'), 'g');
    out = out.replace(re, m => '<mark>' + m + '</mark>');
  }});
  return out;
}}
function showQuotes(bid, axisKey) {{
  const entry = (QUOTES[bid] || {{}})[axisKey];
  if (!entry) return;
  document.getElementById('quoteTitle').textContent = entry.channel + ' · ' + entry.label;
  document.getElementById('quoteSub').textContent = entry.lines.length + '건의 관련 발화';
  document.getElementById('quoteBody').innerHTML = entry.lines.map(l =>
    `<div class="qline"><span class="t">${{mmss(l.t)}}</span>${{highlight(l.text, l.terms)}}</div>`
  ).join('');
  document.getElementById('quoteOverlay').classList.add('open');
}}
document.addEventListener('click', (e) => {{
  const td = e.target.closest('[data-bid][data-axis]');
  if (td) showQuotes(td.dataset.bid, td.dataset.axis);
}});
document.getElementById('quoteClose').addEventListener('click', () => {{
  document.getElementById('quoteOverlay').classList.remove('open');
}});
document.getElementById('quoteOverlay').addEventListener('click', (e) => {{
  if (e.target.id === 'quoteOverlay') e.target.classList.remove('open');
}});
</script>
</body></html>"""


def render_json(res: ComparisonResult) -> str:
    return json.dumps(
        {
            "product_name": res.product_name,
            "differentiating_axes": res.differentiating_axes,
            "metrics": [m.to_dict() for m in res.metrics],
            "axes": [
                {
                    "key": a.key, "label": a.label, "mean": a.mean, "spread": a.spread,
                    "leader": a.leader, "values": a.values, "index": a.index, "zscore": a.zscore,
                }
                for a in res.axes
            ],
            "distinctive": res.distinctive,
            "profile": res.profile,
        },
        ensure_ascii=False,
        indent=2,
    )
