"""HTML for the watch page and for a standalone export."""
from __future__ import annotations

import html
import json

LABEL = {
    "traced": "from a tool output",
    "stated": "from your message",
    "near": "close to a source, not equal",
    "unfound": "not found in any source",
}

CSS = """
:root{--bg:#f6f5f1;--card:#fff;--ink:#1c1c1a;--mute:#6b6a64;--line:#e2e0d8;
--ok:#1f7a4d;--okbg:#e3f2e9;--st:#2f5bd3;--stbg:#e6ecfb;--warn:#a5600b;--warnbg:#fdeccc;--bad:#b42318;--badbg:#fde3e0}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#16171a;--card:#1f2125;--ink:#e8e6e1;--mute:#9a988f;--line:#33353a;
--okbg:#163526;--ok:#6fd3a0;--stbg:#1b2747;--st:#8fb0ff;--warnbg:#3a2a0e;--warn:#f0b45a;--badbg:#3d1714;--bad:#ff8a7a}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 Inter,system-ui,sans-serif}
a{color:inherit}.mono{font-family:"JetBrains Mono",ui-monospace,monospace}
.n{border-radius:4px;padding:0 3px;font-weight:600;cursor:pointer}
.n.traced{background:var(--okbg);color:var(--ok)}.n.stated{background:var(--stbg);color:var(--st)}
.n.near{background:var(--warnbg);color:var(--warn)}.n.unfound{background:var(--badbg);color:var(--bad)}
.n.sel{outline:2px solid currentColor}
.doc{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:28px 32px;white-space:pre-wrap;
font:16px/1.65 "Source Serif 4",Georgia,serif;overflow-wrap:anywhere}
.pills{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 16px}
.pill{font:600 12px "JetBrains Mono",monospace;padding:4px 9px;border-radius:999px}
.pill.traced{background:var(--okbg);color:var(--ok)}.pill.stated{background:var(--stbg);color:var(--st)}
.pill.near{background:var(--warnbg);color:var(--warn)}.pill.unfound{background:var(--badbg);color:var(--bad)}
.src{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}
.src h3{margin:0 0 4px;font-size:14px}.src .meta{color:var(--mute);font-size:12.5px;margin-bottom:10px}
.src pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12.5px/1.5 "JetBrains Mono",monospace;margin:0;
background:var(--bg);border-radius:8px;padding:10px 12px;max-height:340px;overflow:auto}
.src mark{background:var(--okbg);color:var(--ok);font-weight:700}
.muted{color:var(--mute)}
"""

FONTS = ('<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700'
         '&family=JetBrains+Mono:wght@400;600&family=Source+Serif+4:wght@400;600&display=swap" rel="stylesheet">')


def doc_fragment(text: str, hits: list[dict]) -> str:
    """The document text with every checked number wrapped in a coloured span."""
    out, pos = [], 0
    for i, h in enumerate(sorted(hits, key=lambda h: h["offset"])):
        if h["status"] == "ignored" or h["offset"] < pos:
            continue
        start, end = h["offset"], h["offset"] + len(h["raw"])
        out.append(html.escape(text[pos:start]))
        title = LABEL[h["status"]]
        out.append(f'<span class="n {h["status"]}" data-i="{i}" title="{title}">{html.escape(text[start:end])}</span>')
        pos = end
    out.append(html.escape(text[pos:]))
    return "".join(out)


def pills(summary: dict) -> str:
    parts = []
    for k in ("traced", "stated", "near", "unfound"):
        if summary.get(k):
            parts.append(f'<span class="pill {k}">{summary[k]} {LABEL[k]}</span>')
    return '<div class="pills">' + "".join(parts) + "</div>"


def source_info(conn, hits: list[dict]) -> list[dict | None]:
    """Per hit (same order as sorted-by-offset), the source label and a snippet around the number."""
    ordered = sorted(hits, key=lambda h: h["offset"])
    ids = {h["source_id"] for h in ordered if h.get("source_id")}
    rows = {}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in conn.execute(f"SELECT id, kind, tool, label, ts, text FROM sources WHERE id IN ({marks})", list(ids)):
            rows[r["id"]] = r
    info = []
    for h in ordered:
        r = rows.get(h.get("source_id"))
        if not r:
            info.append({"status": h["status"], "raw": h["raw"], "line": h["line"]})
            continue
        t, off = r["text"], h["source_offset"] or 0
        a, b = max(0, off - 220), min(len(t), off + 220)
        end = off
        while end < len(t) and (t[end].isdigit() or t[end] in ".,%"):
            end += 1
        snippet = (("..." if a else "") + html.escape(t[a:off]) + "<mark>" + html.escape(t[off:end]) + "</mark>"
                   + html.escape(t[end:b]) + ("..." if b < len(t) else ""))
        info.append({"status": h["status"], "raw": h["raw"], "line": h["line"], "kind": r["kind"],
                     "tool": r["tool"], "label": r["label"], "ts": r["ts"], "snippet": snippet,
                     "source_value": h.get("source_value")})
    return info


SOURCE_JS = """
function showSource(i){
  document.querySelectorAll('.n.sel').forEach(e=>e.classList.remove('sel'));
  const el=document.querySelector('.n[data-i="'+i+'"]'); if(el) el.classList.add('sel');
  const s=INFO[i], box=document.getElementById('src');
  if(!s){return}
  const label={traced:'From a tool output',stated:'From your message',near:'Close to a source, not equal',unfound:'Not found in any source'}[s.status];
  if(!s.snippet){box.innerHTML='<h3>'+label+'</h3><div class="meta">Line '+s.line+'. No tool output or message in this work contains '+s.raw+
    '. That does not make it false: it may have been worked out without a tool, or come from somewhere not recorded.</div>';return}
  const near=s.status==='near'?'<div class="meta">The source says '+s.source_value+', the document says '+s.raw+'.</div>':'';
  box.innerHTML='<h3>'+label+'</h3><div class="meta">Line '+s.line+' &middot; '+(s.kind==='user'?'you':s.tool)+' &middot; '+s.ts+
    '</div>'+near+'<div class="meta mono">'+escapeHtml(s.label)+'</div><pre>'+s.snippet+'</pre>';
}
function escapeHtml(x){return (x||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))}
document.addEventListener('click',e=>{const n=e.target.closest('.n'); if(n) showSource(+n.dataset.i)});
"""


def export_page(path: str, text: str, hits: list[dict], summary: dict, info: list) -> str:
    """One self-contained HTML file: the document with its numbers coloured and their sources."""
    n = summary["traced"] + summary["stated"]
    total = n + summary["near"] + summary["unfound"]
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(path.rsplit('/', 1)[-1])}</title>{FONTS}<style>{CSS}
.wrap{{max-width:1180px;margin:0 auto;padding:28px 16px;display:grid;grid-template-columns:minmax(0,1fr) 380px;gap:24px;align-items:start}}
.side{{position:sticky;top:20px}}@media(max-width:900px){{.wrap{{grid-template-columns:1fr}}.side{{position:static}}}}</style></head>
<body><div class="wrap"><main><div class="muted mono" style="font-size:12px">{html.escape(path)}</div>
<h1 style="margin:4px 0 0;font-size:24px">{n} of {total} numbers traced to a source</h1>{pills(summary)}
<div class="doc">{doc_fragment(text, hits)}</div></main>
<aside class="side"><div class="src" id="src"><h3>Click a number</h3><div class="meta">Each coloured number links to the command
output, file or message it came from.</div></div>
<p class="muted" style="font-size:12px">Checked by claimtrail. "Not found" means no recorded source has the number. It does
not mean the number is false.</p></aside></div>
<script>const INFO={json.dumps(info)};{SOURCE_JS}</script></body></html>"""
