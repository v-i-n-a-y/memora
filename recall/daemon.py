"""Memora recall daemon.

Loads the memora package (and its embedding backend) once, then serves
semantic-search requests over a unix socket so the per-prompt hook client
stays fast instead of paying model/import startup per call.

Context-aware recall:
- Per-session rolling topic vector (EMA of query embeddings) is blended into
  each search so short follow-up prompts inherit the session's topic.
- The hook passes cwd; results tagged with the matching focus:<area> get a
  small score boost.

Protocol: one JSON object per line on the socket.
  request:  {"query": str, "top_k": int?, "min_score": float?,
             "cwd": str?, "session_id": str?}
  response: {"results": [{"score": float, "id": int, "content": str, "tags": [...]}]}
"""

import json
import os
import socket
import sys
import time

_HDIR = os.path.dirname(os.path.abspath(__file__))

SOCK_PATH = os.path.join(_HDIR, "daemon.sock")
LOG_PATH = os.path.join(_HDIR, "recall_log.jsonl")
IDLE_EXIT_SECONDS = 6 * 3600

# Embeddings run locally via sentence-transformers (no Ollama). Internal LLM disabled.
os.environ.setdefault("MEMORA_EMBEDDING_MODEL", "sentence-transformers")
os.environ.setdefault("SENTENCE_TRANSFORMERS_MODEL", "BAAI/bge-small-en-v1.5")
os.environ.setdefault("MEMORA_LLM_ENABLED", "0")

# How much the session's rolling topic vector contributes to the search.
CONTEXT_WEIGHT = 0.25
# EMA factor: rolling = (1-alpha)*rolling + alpha*current
CONTEXT_ALPHA = 0.5
FOCUS_BOOST = 1.15
CWD_FOCUS_MAP = {
    "evandor": "focus:evandor",
    "doctorate": "focus:phd",
    "perseus": "focus:phd",
    "osiris": "focus:phd",
    "astrodynamic": "focus:astrodynamic",
}

# Short-term store (Tier 1). The daemon ingests each captured turn here using
# its already-warm bge model, so ingest is per-turn with no model reload.
STDB = os.path.join(_HDIR, "shortterm.db")
ST_DEDUP_COSINE = 0.85
ST_RECENT_WINDOW = 400
ST_DURABLE_CUES = (
    "always", "never", "from now on", "i prefer", "prefer ", "don't", "do not",
    "make sure", "going forward", "instead of", "stop ", "no longer", "remember",
    "in future", "each time", "whenever", "by default",
)


def st_ingest(req):
    """Ingest one captured turn into the short-term store (local, no LLM)."""
    import sqlite3
    from memora import storage
    from memora.embeddings import cosine_similarity
    text = ((req.get("user") or "") + "\n" + (req.get("assistant") or "")).strip()
    if len(text) < 12:
        return None
    ev = storage._compute_embedding(text, None, [])
    if not ev:
        return None
    sc = sqlite3.connect(STDB)
    sc.execute("PRAGMA busy_timeout=3000")
    sc.execute(
        "create table if not exists episodes(id integer primary key, first_ts real,"
        " last_ts real, seen int, session text, cwd text, user text, assistant text,"
        " emb text, durable int, promoted int default 0)"
    )
    now = time.time()
    dup = None
    for eid, eemb in sc.execute(
        "select id, emb from episodes where promoted=0 order by last_ts desc limit ?",
        (ST_RECENT_WINDOW,),
    ).fetchall():
        try:
            if cosine_similarity(ev, json.loads(eemb)) >= ST_DEDUP_COSINE:
                dup = eid
                break
        except Exception:
            continue
    if dup is not None:
        sc.execute("update episodes set seen=seen+1, last_ts=? where id=?", (now, dup))
        rid = dup
    else:
        cue = 1 if any(q in text.lower() for q in ST_DURABLE_CUES) else 0
        cur = sc.execute(
            "insert into episodes(first_ts,last_ts,seen,session,cwd,user,assistant,emb,durable)"
            " values(?,?,?,?,?,?,?,?,?)",
            (now, now, 1, req.get("session_id"), req.get("cwd"),
             req.get("user", ""), req.get("assistant", ""), json.dumps(ev), cue),
        )
        rid = cur.lastrowid
    sc.commit()
    sc.close()
    return rid


def vec_blend(a, b, wa, wb):
    out = {}
    for k, v in a.items():
        out[k] = v * wa
    for k, v in b.items():
        out[k] = out.get(k, 0.0) + v * wb
    return out


def focus_tag_for_cwd(cwd):
    low = (cwd or "").lower()
    for needle, tag in CWD_FOCUS_MAP.items():
        if needle in low:
            return tag
    return None


def main():
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(SOCK_PATH)
    except OSError:
        # Socket file exists: another daemon may be live, or it is stale.
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(1)
        try:
            probe.connect(SOCK_PATH)
            probe.close()
            sys.exit(0)  # live daemon already serving
        except OSError:
            os.unlink(SOCK_PATH)
            server.bind(SOCK_PATH)

    from memora import storage

    conn = storage.connect(check_same_thread=False)
    # Warm path + trigger any pending embedding rebuild once at startup.
    storage.semantic_search(conn, "warmup", top_k=1)

    server.listen(4)
    server.settimeout(60)
    last_request = time.time()
    session_ctx = {}  # session_id -> rolling topic vector

    while True:
        try:
            client, _ = server.accept()
        except socket.timeout:
            if time.time() - last_request > IDLE_EXIT_SECONDS:
                break
            continue
        last_request = time.time()
        try:
            client.settimeout(10)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = client.recv(65536)
                if not chunk:
                    break
                buf += chunk
            req = json.loads(buf.decode("utf-8"))
            if req.get("op") == "ingest":
                eid = st_ingest(req)
                client.sendall((json.dumps({"ok": True, "id": eid}) + "\n").encode("utf-8"))
                continue
            query = req["query"]
            top_k = int(req.get("top_k", 4))
            min_score = float(req.get("min_score", 0.50))
            session_id = req.get("session_id")

            qvec = storage._compute_embedding(query, None, [])
            if not qvec:
                client.sendall(b'{"results": []}\n')
                continue

            search_vec = qvec
            if session_id and session_id in session_ctx:
                search_vec = vec_blend(
                    qvec, session_ctx[session_id],
                    1.0 - CONTEXT_WEIGHT, CONTEXT_WEIGHT,
                )
            if session_id:
                prev = session_ctx.get(session_id)
                session_ctx[session_id] = (
                    vec_blend(prev, qvec, 1.0 - CONTEXT_ALPHA, CONTEXT_ALPHA)
                    if prev else qvec
                )
                if len(session_ctx) > 50:
                    session_ctx.pop(next(iter(session_ctx)))

            results = storage._search_by_vector(
                conn, search_vec,
                top_k=top_k * 3,  # headroom for boost re-ranking
                min_score=None,
            )
            results = storage.apply_follow(conn, results, "active", is_search=True)

            focus = focus_tag_for_cwd(req.get("cwd"))
            scored = []
            for r in results:
                m = r.get("memory", r)
                score = float(r.get("score", 0))
                tags = m.get("tags") or []
                if focus and focus in tags:
                    score *= FOCUS_BOOST
                scored.append((score, m, tags))
            scored.sort(key=lambda t: t[0], reverse=True)

            out = []
            for score, m, tags in scored:
                if score < min_score or len(out) >= top_k:
                    continue
                out.append({
                    "score": round(score, 3),
                    "id": m.get("id"),
                    "content": (m.get("content") or "")[:600],
                    "tags": tags,
                })
            client.sendall((json.dumps({"results": out}) + "\n").encode("utf-8"))
            # Recall counts as use: feed memora's native importance system
            # (access_count -> logarithmic boost, offsetting time decay).
            for r in out:
                if r["id"] is not None:
                    storage._track_access(conn, r["id"])
            conn.commit()
            with open(LOG_PATH, "a", encoding="utf-8") as log:
                log.write(json.dumps({
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "query": query[:500],
                    "cwd": req.get("cwd"),
                    "recalled": [(r["id"], r["score"]) for r in out],
                }) + "\n")
        except Exception as exc:
            try:
                client.sendall((json.dumps({"error": str(exc)}) + "\n").encode("utf-8"))
            except OSError:
                pass
        finally:
            client.close()

    server.close()
    try:
        os.unlink(SOCK_PATH)
    except OSError:
        pass


if __name__ == "__main__":
    main()
