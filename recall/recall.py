#!/usr/bin/env python3
"""UserPromptSubmit hook: inject semantically relevant memora memories.

Queries the local memora recall daemon over a unix socket. If the daemon is
not running, spawns it in the background and exits silently (the next prompt
will get recall). Never blocks or fails the prompt.
"""

import json
import os
import socket
import subprocess
import sys

_HDIR = os.path.dirname(os.path.abspath(__file__))

SOCK_PATH = os.path.join(_HDIR, "daemon.sock")
DAEMON = os.path.join(_HDIR, "daemon.py")
VENV_PYTHON = "/Users/vinay/.local/share/uv/tools/memora-mcp/bin/python"
TOP_K = 4
MIN_SCORE = 0.62
MIN_PROMPT_LEN = 12


def spawn_daemon():
    subprocess.Popen(
        [VENV_PYTHON, DAEMON],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return

    prompt = (data.get("prompt") or "").strip()
    if len(prompt) < MIN_PROMPT_LEN or prompt.startswith("/"):
        return

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(3)
    try:
        sock.connect(SOCK_PATH)
        sock.sendall((json.dumps({
            "query": prompt[:2000],
            "top_k": TOP_K,
            "min_score": MIN_SCORE,
            "cwd": data.get("cwd"),
            "session_id": data.get("session_id"),
        }) + "\n").encode("utf-8"))
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
        resp = json.loads(buf.decode("utf-8"))
    except OSError:
        spawn_daemon()
        return
    finally:
        sock.close()

    results = resp.get("results") or []
    if not results:
        return

    lines = []
    for r in results:
        tags = ",".join(r.get("tags") or [])
        content = " ".join((r.get("content") or "").split())
        lines.append(f"- [memory {r['id']} | score {r['score']} | {tags}] {content}")

    context = (
        "Auto-recalled memora memories semantically matching this prompt "
        "(background context — may be stale or irrelevant; verify before "
        "relying on specifics, and ignore if not applicable):\n"
        + "\n".join(lines)
    )
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
