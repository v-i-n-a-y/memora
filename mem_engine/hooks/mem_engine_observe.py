#!/usr/bin/env python3
"""Claude Code Stop hook (OPT-IN): ship the finished turn to mem_engine working memory.

This is the per-turn "capture" half of the engine loop. It is fire-and-forget over
SSH and NEVER blocks or fails the turn — if the server is unreachable it silently
gives up. Promotion to long-term happens later via the server-side nightly
consolidate cron (so this hook costs only a backgrounded SSH per turn, no LLM).

NOTE: this re-introduces an always-on Stop hook (the pattern retired 2026-06-11).
It is deliberately opt-in. Enable by adding to ~/.claude/settings.json:

    {
      "hooks": {
        "Stop": [
          {"hooks": [{"type": "command",
                      "command": "python3 ~/Documents/Projects/memora/mem_engine/hooks/mem_engine_observe.py"}]}
        ]
      }
    }

Disable by removing that entry. Transport assumes the `vinaysecureserver` SSH alias
and the `memora` container (adjust SSH_HOST / CONTAINER below if they change).
"""
import json
import os
import subprocess
import sys

SSH_HOST = "vinaysecureserver"
CONTAINER = "memora"
VENV_PY = "/app/.venv/bin/python"
MIN_LEN = 12
MAX_CHARS = 6000

_REMOTE = (
    "import sys,json;"
    "from mem_engine.mcp_tools import tool_observe;"
    "d=json.load(sys.stdin);"
    "tool_observe(d['t'], session=d.get('s'), cwd=d.get('c'), kind='turn')"
)


def _last_texts(transcript_path):
    """Return (last_user_text, last_assistant_text) from a Claude Code transcript."""
    last_user = last_asst = ""
    try:
        with open(transcript_path) as f:
            for line in f:
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                msg = ev.get("message") or ev
                role = msg.get("role") or ev.get("role")
                content = msg.get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = " ".join(
                        b.get("text", "") for b in content
                        if isinstance(b, dict) and b.get("type") == "text"
                    )
                else:
                    text = ""
                text = text.strip()
                if not text:
                    continue
                if role == "user":
                    last_user = text
                elif role == "assistant":
                    last_asst = text
    except Exception:
        pass
    return last_user, last_asst


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    tpath = data.get("transcript_path")
    if not tpath or not os.path.exists(tpath):
        return
    user, asst = _last_texts(tpath)
    turn = (user + "\n" + asst).strip()
    if len(turn) < MIN_LEN:
        return
    payload = json.dumps({"t": turn[:MAX_CHARS],
                          "s": data.get("session_id") or "",
                          "c": data.get("cwd") or ""})
    try:
        p = subprocess.Popen(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", SSH_HOST,
             "docker", "exec", "-i", CONTAINER, VENV_PY, "-c", _REMOTE],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach: don't block the turn
        )
        p.stdin.write(payload.encode("utf-8"))
        p.stdin.close()
    except Exception:
        pass  # never let capture failure affect the session


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
