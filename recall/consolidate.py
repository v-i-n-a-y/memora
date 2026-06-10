#!/usr/bin/env python3
"""Two-tier consolidator (mem0-style working -> long-term memory).

TIER 1  short-term, LOCAL, NO LLM:
  Ingest captured turns into a short-term SQLite store (shortterm.db) using the
  local bge embeddings, deduping near-identical observations and counting
  recurrence. Cheap; ephemeral chatter lives here and expires by TTL without
  ever costing a Claude token.

TIER 2  promotion -> long-term, LLM, RARE:
  Observations that PERSIST (recurred >=2x, or look like a standing preference
  and have dwelt past PERSIST_HOURS) are distilled by ONE headless `claude -p`
  (with memora MCP tools) into clean long-term memora memories, deduped against
  the main store. `claude -p` runs ONLY when something durable is ready, so
  token use tracks genuine durability.

Promotion (the only token-spending tier) is gated behind an enable flag file so
it stays off until you opt in:  touch recall/AUTOWRITE_ON

Run under the memora venv python (needs sentence-transformers + memora).
Launched in the background by capture.py; self-locking; standalone-runnable.
"""
import json
import os
import sqlite3
import subprocess
import time

_HDIR = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(_HDIR, "capture_queue.jsonl")
STDB = os.path.join(_HDIR, "shortterm.db")
LOCK = os.path.join(_HDIR, "consolidate.lock")
RUNLOG = os.path.join(_HDIR, "consolidate_runs.log")
DISTILL_PROMPT = os.path.join(_HDIR, "distill_prompt.md")
ENABLE_FLAG = os.path.join(_HDIR, "AUTOWRITE_ON")

DEDUP_COSINE = 0.85    # near-duplicate threshold for short-term recurrence
PERSIST_HOURS = 12     # a durable-looking one-off promotes after dwelling this long
EXPIRE_DAYS = 14       # short-term TTL for observations that never persist
PROMOTE_BATCH = 12     # max episodes distilled per claude -p run
RECENT_WINDOW = 400    # how many recent episodes to compare against for recurrence

DURABLE_CUES = (
    "always", "never", "from now on", "i prefer", "prefer ", "don't", "do not",
    "make sure", "going forward", "instead of", "stop ", "no longer", "remember",
    "in future", "each time", "whenever", "by default",
)

os.environ.setdefault("MEMORA_EMBEDDING_MODEL", "sentence-transformers")
os.environ.setdefault("SENTENCE_TRANSFORMERS_MODEL", "BAAI/bge-small-en-v1.5")
os.environ.setdefault("MEMORA_LLM_ENABLED", "0")


def log(m):
    try:
        with open(RUNLOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {m}\n")
    except Exception:
        pass


def _db():
    c = sqlite3.connect(STDB)
    c.execute(
        """create table if not exists episodes(
            id integer primary key, first_ts real, last_ts real, seen int,
            session text, cwd text, user text, assistant text, emb text,
            durable int, promoted int default 0)"""
    )
    return c


def _embed(text):
    from memora.embeddings import compute_embedding
    return compute_embedding(text, None, [], "sentence-transformers")


def _cos(a, b):
    from memora.embeddings import cosine_similarity
    return cosine_similarity(a, b)


def ingest(c):
    """Tier 1: drain the capture queue into short-term store (local, no LLM)."""
    if not os.path.exists(QUEUE) or os.path.getsize(QUEUE) == 0:
        return 0
    with open(QUEUE) as f:
        lines = [l for l in f if l.strip()]
    open(QUEUE, "w").close()

    recent = [
        (r[0], json.loads(r[1]))
        for r in c.execute(
            "select id, emb from episodes where promoted=0 order by last_ts desc limit ?",
            (RECENT_WINDOW,),
        ).fetchall()
    ]
    n = 0
    for l in lines:
        try:
            t = json.loads(l)
        except Exception:
            continue
        text = (t.get("user", "") + "\n" + t.get("assistant", "")).strip()
        if len(text) < 12:
            continue
        ev = _embed(text)
        now = time.time()
        dup = next((eid for eid, eemb in recent if _cos(ev, eemb) >= DEDUP_COSINE), None)
        if dup is not None:
            c.execute("update episodes set seen=seen+1, last_ts=? where id=?", (now, dup))
        else:
            cue = 1 if any(q in text.lower() for q in DURABLE_CUES) else 0
            cur = c.execute(
                "insert into episodes(first_ts,last_ts,seen,session,cwd,user,assistant,emb,durable)"
                " values(?,?,?,?,?,?,?,?,?)",
                (now, now, 1, t.get("session_id"), t.get("cwd"),
                 t.get("user", ""), t.get("assistant", ""), json.dumps(ev), cue),
            )
            recent.insert(0, (cur.lastrowid, ev))
        n += 1
    c.commit()
    return n


def ready_episodes(c):
    """Episodes that have persisted and are ready for long-term promotion."""
    now = time.time()
    out = []
    for eid, user, asst, seen, durable, first in c.execute(
        "select id, user, assistant, seen, durable, first_ts from episodes where promoted=0"
    ).fetchall():
        age_h = (now - first) / 3600.0
        if seen >= 2 or (durable and age_h >= PERSIST_HOURS):
            out.append({"id": eid, "user": user, "assistant": asst,
                        "seen": seen, "age_hours": round(age_h, 1)})
    return out[:PROMOTE_BATCH]


def promote(c):
    """Tier 2: distill persistent episodes into long-term memora (LLM, gated)."""
    ready = ready_episodes(c)
    if not ready:
        return 0
    if not os.path.exists(ENABLE_FLAG):
        log(f"promotion disabled (no AUTOWRITE_ON): {len(ready)} episode(s) ready, awaiting opt-in")
        return 0
    payload = os.path.join(_HDIR, "promote_batch.json")
    json.dump(ready, open(payload, "w"))
    try:
        prompt = open(DISTILL_PROMPT).read().replace("{{PAYLOAD}}", payload)
    except Exception:
        prompt = (f"Read {payload} (a JSON list of persistent observations) and distill the "
                  "genuinely durable facts into memora memories via memory_create, deduping "
                  "against existing memories. Follow the memora-usage-conventions memory.")
    try:
        out = subprocess.run(
            ["claude", "-p", prompt,
             "--allowedTools", "mcp__memora-server,mcp__memora-server__*,Read,ToolSearch",
             "--max-turns", "40"],
            capture_output=True, text=True, timeout=600,
            env={**os.environ, "CLAUDE_DREAM_RUN": "1"},
        )
    except Exception as e:
        log(f"promote claude -p failed: {e}; episodes retained for retry")
        return 0
    if out.returncode != 0:
        log(f"promote claude -p exit {out.returncode}; episodes retained. tail={ (out.stderr or '')[-160:] !r}")
        return 0
    ids = [r["id"] for r in ready]
    c.execute(f"update episodes set promoted=1 where id in ({','.join('?' * len(ids))})", ids)
    c.commit()
    summary = " ".join((out.stdout or "").split())[:500]
    log(f"promoted {len(ids)} episode(s) to long-term memora | distill: {summary}")
    return len(ids)


def expire(c):
    cutoff = time.time() - EXPIRE_DAYS * 86400
    c.execute(
        "delete from episodes where last_ts < ? and (promoted=1 or (durable=0 and seen<2))",
        (cutoff,),
    )
    c.commit()


def main():
    if os.path.exists(LOCK):
        return
    try:
        open(LOCK, "w").close()
    except Exception:
        return
    try:
        c = _db()
        ni = ingest(c)
        npr = promote(c)
        expire(c)
        if ni or npr:
            log(f"ingested {ni} turn(s); promoted {npr}")
    except Exception as e:
        log(f"error: {e}")
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == "__main__":
    main()
