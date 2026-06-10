#!/usr/bin/env python3
"""Stop hook: capture the completed turn (last user prompt + assistant reply)
to a queue for later background fact-extraction (mem0-style "add").

Also the single EVENT DRIVER for the learning loop (no cron): once enough turns
accumulate it launches the consolidator in the background, and roughly daily it
launches the store-maintenance dream. Both are detached, self-locking, and run
while the session is still alive so the nested `claude -p` survives.

Never blocks and never fails the turn.
"""
import json
import os
import subprocess
import sys
import time

_HDIR = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(_HDIR, "capture_queue.jsonl")
CONSOLIDATE_LOCK = os.path.join(_HDIR, "consolidate.lock")
DREAM_STAMP = os.path.join(_HDIR, "dream_runs.log")
# consolidate.py needs the memora venv python (sentence-transformers + memora);
# the others are stdlib and run under whatever python invokes this hook.
VENV_PYTHON = "/Users/vinay/.local/share/uv/tools/memora-mcp/bin/python"

MAX_CHARS = 6000
CONSOLIDATE_THRESHOLD = 6   # turns queued before a background extraction runs
DREAM_EVERY_HOURS = 20      # daily-ish store maintenance, event-triggered


def _text_from_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                parts.append(b["text"])
        return "\n".join(parts)
    return ""


def _last_user_prompt(transcript_path):
    """Scan the transcript JSONL for the most recent genuine user message."""
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
                    # skip tool-result-only turns and command/system noise
                    if t and not t.startswith("<") and not t.startswith("/"):
                        last = t
    except Exception:
        pass
    return last


def _count_lines(path):
    try:
        with open(path) as f:
            return sum(1 for _ in f)
    except Exception:
        return 0


def _stale(path, hours):
    try:
        return (time.time() - os.path.getmtime(path)) > hours * 3600
    except Exception:
        return True  # never run -> treat as stale


def _launch(cmd):
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={**os.environ, "CLAUDE_DREAM_RUN": "1"},
    )


def _maybe_launch():
    if _count_lines(QUEUE) >= CONSOLIDATE_THRESHOLD and not os.path.exists(CONSOLIDATE_LOCK):
        _launch([VENV_PYTHON, os.path.join(_HDIR, "consolidate.py")])
    if _stale(DREAM_STAMP, DREAM_EVERY_HOURS):
        _launch(["/bin/bash", os.path.join(_HDIR, "dream.sh")])


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("stop_hook_active") or os.environ.get("CLAUDE_DREAM_RUN"):
        return
    transcript = data.get("transcript_path") or ""
    user = _last_user_prompt(transcript) if transcript else ""
    assistant = data.get("last_assistant_message") or ""
    u = user.strip()
    if len(u) < 12:
        return
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "session_id": data.get("session_id"),
        "cwd": data.get("cwd"),
        "user": u[:MAX_CHARS],
        "assistant": (assistant or "")[:MAX_CHARS],
    }
    try:
        with open(QUEUE, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        return
    _maybe_launch()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
