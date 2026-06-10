#!/usr/bin/env python3
"""Tiny web viewer for the SHORT-TERM memory store (shortterm.db).

Serves a force-directed graph at http://localhost:8766/graph that mirrors
memora's long-term graph (http://localhost:8765/graph) but for the working tier:
  - nodes  = short-term observations (size ~ recurrence count; colour by
             promoted / ready-to-promote / durable-cue / transient)
  - edges  = semantic similarity (bge cosine >= EDGE_THRESH), so recurring
             topic clusters are visible at a glance.

Run under the memora venv python (uses memora.embeddings.cosine_similarity).
Stateless and read-only.
"""
import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

_HDIR = os.path.dirname(os.path.abspath(__file__))
STDB = os.path.join(_HDIR, "shortterm.db")
PORT = 8766
EDGE_THRESH = 0.55
PERSIST_HOURS = 12

os.environ.setdefault("MEMORA_EMBEDDING_MODEL", "sentence-transformers")
os.environ.setdefault("SENTENCE_TRANSFORMERS_MODEL", "BAAI/bge-small-en-v1.5")


def _cos(a, b):
    from memora.embeddings import cosine_similarity
    return cosine_similarity(a, b)


def build_graph():
    if not os.path.exists(STDB):
        return {"nodes": [], "links": []}
    c = sqlite3.connect(STDB)
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute(
            "select id,user,seen,durable,promoted,first_ts,emb from episodes"
        ).fetchall()
    except Exception:
        return {"nodes": [], "links": []}
    now = time.time()
    nodes, embs = [], []
    for r in rows:
        age_h = round((now - r["first_ts"]) / 3600.0, 1)
        ready = bool(r["seen"] >= 2 or (r["durable"] and (now - r["first_ts"]) >= PERSIST_HOURS * 3600))
        nodes.append({
            "id": r["id"], "label": (r["user"] or "")[:70], "seen": r["seen"],
            "durable": int(r["durable"]), "promoted": int(r["promoted"]),
            "ready": int(ready), "age_h": age_h,
        })
        try:
            embs.append(json.loads(r["emb"]))
        except Exception:
            embs.append({})
    links = []
    for i in range(len(nodes)):
        for j in range(i + 1, len(nodes)):
            s = _cos(embs[i], embs[j])
            if s >= EDGE_THRESH:
                links.append({"source": nodes[i]["id"], "target": nodes[j]["id"], "value": round(s, 3)})
    return {"nodes": nodes, "links": links}


HTML = """<!DOCTYPE html><html><head><meta charset=utf-8><title>Memora - short-term</title>
<style>body{margin:0;background:#0e1116;color:#cdd3da;font:13px system-ui,Segoe UI,Arial}
#hdr{position:fixed;top:8px;left:12px;z-index:10;line-height:1.6}#hdr b{color:#e6edf3;font-size:14px}
.k{margin-right:12px;white-space:nowrap}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:4px;vertical-align:middle}
#empty{position:fixed;top:50%;left:0;right:0;text-align:center;color:#6e7681}</style>
<script src="https://unpkg.com/force-graph"></script></head>
<body><div id=hdr><b>Short-term memory</b> (working tier)<br>
<span class=k><span class=dot style="background:#2ea043"></span>promoted to long-term</span>
<span class=k><span class=dot style="background:#d29922"></span>ready to promote</span>
<span class=k><span class=dot style="background:#e3b341"></span>durable cue</span>
<span class=k><span class=dot style="background:#6e7681"></span>transient</span>
&nbsp; node size = recurrence &middot; edges = semantic similarity</div>
<div id=empty></div><div id=g></div><script>
function colour(n){return n.promoted?'#2ea043':n.ready?'#d29922':n.durable?'#e3b341':'#6e7681'}
fetch('/api/graph').then(r=>r.json()).then(d=>{
 if(!d.nodes.length){document.getElementById('empty').textContent='Short-term store is empty - it fills as turns are captured and ingested.';return}
 ForceGraph()(document.getElementById('g')).graphData(d)
  .nodeLabel(n=>`${n.label}<br>seen ${n.seen} &middot; age ${n.age_h}h${n.promoted?' &middot; promoted':n.ready?' &middot; ready':''}`)
  .nodeColor(colour).nodeVal(n=>2+n.seen*3)
  .linkColor(()=>'rgba(120,140,170,.25)').linkWidth(l=>l.value*2)
  .backgroundColor('#0e1116');
});
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/api/graph"):
            body = json.dumps(build_graph()).encode()
            ct = "application/json"
        elif self.path.startswith("/graph") or self.path == "/":
            body = HTML.encode()
            ct = "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", PORT), H).serve_forever()
