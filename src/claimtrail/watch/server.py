"""`claimtrail watch`: a local page that shows every document the agent wrote, live."""
from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from claimtrail.watch import db, render


def doc_html(conn, path: str) -> str | None:
    row = conn.execute("SELECT text, report FROM docs WHERE path = ?", (path,)).fetchone()
    if not row:
        return None
    hits = json.loads(row["report"])
    summary = {k: 0 for k in ("traced", "stated", "reported", "near", "unfound", "ignored")}
    for h in hits:
        summary[h["status"]] += 1
    return render.export_page(path, row["text"], hits, summary, render.source_info(conn, hits))


def docs_list(conn) -> list[dict]:
    out = []
    for r in conn.execute("SELECT path, cwd, ts, report FROM docs ORDER BY ts DESC LIMIT 200"):
        counts = {k: 0 for k in ("traced", "stated", "near", "unfound")}
        for h in json.loads(r["report"]):
            if h["status"] in counts:
                counts[h["status"]] += 1
        out.append({"path": r["path"], "cwd": r["cwd"], "ts": r["ts"], **counts})
    return out


INDEX = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>claimtrail watch</title>""" + render.FONTS + "<style>" + render.CSS + """
body{display:grid;grid-template-columns:300px 1fr;height:100vh}
nav{border-right:1px solid var(--line);overflow:auto;padding:18px 12px}
nav h1{font-size:18px;margin:0 6px 2px}nav p{margin:0 6px 14px;font-size:12.5px}
.item{display:block;padding:9px 10px;border-radius:8px;text-decoration:none;margin-bottom:4px}
.item:hover,.item.on{background:var(--card)}.item b{display:block;font-size:13.5px;overflow-wrap:anywhere}
.item small{color:var(--mute);font-size:11.5px}.dots{display:flex;gap:6px;margin-top:4px;font:600 11px "JetBrains Mono",monospace}
.dots span{padding:1px 6px;border-radius:999px}iframe{border:0;width:100%;height:100vh}
.empty{padding:40px;color:var(--mute)}
@media(max-width:800px){body{grid-template-columns:1fr;height:auto}nav{border-right:0;border-bottom:1px solid var(--line)}iframe{height:80vh}}
</style></head><body><nav><h1>claimtrail</h1><p class="muted">Documents your agents wrote, newest first. Updates live.</p>
<div id="list"></div></nav><main id="main"><div class="empty">No documents yet. When an agent writes a .md, .html, .txt or
.tex file, it shows up here with every number checked against what the agent actually ran and read.</div></main>
<script>
let cur=null, curTs=null;
function esc(x){return x.replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function tick(){
  const docs=await (await fetch('/api/docs')).json();
  document.getElementById('list').innerHTML=docs.map(d=>{
    const name=d.path.split('/').pop(), dir=d.path.split('/').slice(-3,-1).join('/');
    const dots=[['traced','okbg','ok'],['stated','stbg','st'],['near','warnbg','warn'],['unfound','badbg','bad']]
      .filter(k=>d[k[0]]).map(k=>'<span style="background:var(--'+k[1]+');color:var(--'+k[2]+')">'+d[k[0]]+'</span>').join('');
    return '<a class="item'+(d.path===cur?' on':'')+'" href="#" data-p="'+esc(d.path)+'"><b>'+esc(name)+'</b><small>'+esc(dir)+' &middot; '+d.ts.slice(11,16)+' UTC</small><div class="dots">'+dots+'</div></a>'}).join('');
  if(!cur && docs.length) open_(docs[0].path, docs[0].ts);
  const d=docs.find(x=>x.path===cur); if(d && d.ts!==curTs) open_(d.path, d.ts);
}
function open_(p, ts){cur=p; curTs=ts; document.getElementById('main').innerHTML='<iframe src="/doc?path='+encodeURIComponent(p)+'"></iframe>';}
document.addEventListener('click',e=>{const a=e.target.closest('.item'); if(!a) return; e.preventDefault(); open_(a.dataset.p, null); tick();});
tick(); setInterval(tick, 2000);
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, body: str, ctype="text/html; charset=utf-8", code=200):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        url = urlparse(self.path)
        conn = db.connect()
        try:
            if url.path == "/":
                self._send(INDEX)
            elif url.path == "/api/docs":
                self._send(json.dumps(docs_list(conn)), "application/json")
            elif url.path == "/doc":
                path = parse_qs(url.query).get("path", [""])[0]
                page = doc_html(conn, path)
                self._send(page or f"<p>No record for {html.escape(path)}</p>", code=200 if page else 404)
            else:
                self._send("not found", "text/plain", 404)
        finally:
            conn.close()

    def log_message(self, *args):
        pass


def serve(port: int = 7171) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"claimtrail watch: http://127.0.0.1:{port}  (store: {db.db_path()})")
    server.serve_forever()
