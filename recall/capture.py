#!/usr/bin/env python3
"""Stop hook: capture each completed turn and ingest it into the short-term
memory store IMMEDIATELY, every turn, by handing it to the already-warm recall
daemon (which has bge loaded). No model reload is paid per turn.

Also the event driver (no cron):
- promotion (Tier 2, claude -p) is launched only when short-term actually has a
  promotable observation AND autowrite is enabled (AUTOWRITE_ON);
- the maintenance dream runs roughly daily.

Never blocks and never fails the turn.
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time

_HDIR = os.path.dirname(os.path.abspath(__file__))
SOCK = os.path.join(_HDIR, "daemon.sock")
DAEMON = os.path.join(_HDIR, "daemon.py")
QUEUE = os.path.join(_HDIR, "capture_queue.jsonl")  # fallback only (daemon down)
STDB = os.path.join(_HDIR, "shortterm.db")
CONSOLIDATE = os.path.join(_HDIR, "consolidate.py")
CONSOLIDATE_LOCK = os.path.join(_HDIR, "consolidate.lock")
ENABLE_FLAG = os.path.join(_HDIR, "AUTOWRITE_ON")
DREAM_STAMP = os.path.join(_HDIR, "dream_runs.log")
DREAM = os.path.join(_HDIR, "dream.sh")
VENV_PYTHON = "/Users/vinay/.local/share/uv/tools/memora-mcp/bin/python"

MAX_CHARS = 6000
PERSIST_HOURS = 12
DREAM_EVERY_HOURS = 20


def _text_from_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b["text"] for b in content
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        )
    return ""


def _last_user_prompt(transcript_path):
    last = ""
    try:
        with open(transcript_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                msg = o.get("message") or {}
                if o.get("type") == "user" or msg.get("role") == "user":
                    t = _text_from_content(msg.get("content")).strip()
                    if t and not t.startswith("<") and not t.startswith("/"):
                        last = t
    except Exception:
        pass
    return last


def _ingest_via_daemon(rec):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(4)
        s.connect(SOCK)
        s.sendall((json.dumps({"op": "ingest", **rec}) + "\n").encode("utf-8"))
        s.recv(8192)
        s.close()
        return True
    except OSError:
        return False


def _spawn_daemon():
    try:
        subprocess.Popen([VENV_PYTHON, DAEMON], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         start_new_session=True)
    except Exception:
        pass


def _queue_fallback(rec):
    try:
        with open(QUEUE, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _has_promotable():
    """Readiness check via plain SQLite (no embeddings): any unpromoted episode
    that has recurred (>=2) or is a durable cue dwelt past PERSIST_HOURS."""
    try:
        c = sqlite3.connect(STDB)
        row = c.execute(
            "select 1 from episodes where promoted=0 and "
            "(seen>=2 or (durable=1 and ?-first_ts>=?)) limit 1",
            (time.time(), PERSIST_HOURS * 3600),
        ).fetchone()
        c.close()
        return row is not None
    except Exception:
        return False


def _launch(cmd):
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True,
                     env={**os.environ, "CLAUDE_DREAM_RUN": "1"})


def _stale(path, hours):
    try:
        return (time.time() - os.path.getmtime(path)) > hours * 3600
    except Exception:
        return True


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("stop_hook_active") or os.environ.get("CLAUDE_DREAM_RUN"):
        return
    transcript = data.get("transcript_path") or ""
    user = _last_user_prompt(transcript) if transcript else ""
    u = user.strip()
    if len(u) < 12:
        return
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session_id": data.get("session_id"),
        "cwd": data.get("cwd"),
        "user": u[:MAX_CHARS],
        "assistant": (data.get("last_assistant_message") or "")[:MAX_CHARS],
    }
    # Tier 1: ingest THIS turn now, via the warm daemon.
    if not _ingest_via_daemon(rec):
        _queue_fallback(rec)   # daemon down: keep it for a later drain
        _spawn_daemon()        # and bring the daemon up for next turn

    # Tier 2: promote only when something is actually ready and autowrite is on.
    if (os.path.exists(ENABLE_FLAG) and _has_promotable()
            and not os.path.exists(CONSOLIDATE_LOCK)):
        _launch([VENV_PYTHON, CONSOLIDATE])

    # Store maintenance, roughly daily, event-triggered (no cron).
    if _stale(DREAM_STAMP, DREAM_EVERY_HOURS):
        _launch(["/bin/bash", DREAM])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
