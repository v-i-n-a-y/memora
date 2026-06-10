#!/usr/bin/env python3
"""Stop hook: occasionally nudge Claude to persist durable session facts to memora.

Rate-limited to once per 30 minutes; never fires during the nightly dream run
or when re-entered (stop_hook_active)."""

import json
import os
import sys
import time

_HDIR = os.path.dirname(os.path.abspath(__file__))

STATE = os.path.join(_HDIR, "last_nudge")
INTERVAL = 30 * 60


def main():
    if os.environ.get("CLAUDE_DREAM_RUN"):
        return
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if data.get("stop_hook_active"):
        return
    try:
        if time.time() - os.path.getmtime(STATE) < INTERVAL:
            return
    except OSError:
        pass
    with open(STATE, "w") as f:
        f.write(str(int(time.time())))
    print(json.dumps({
        "decision": "block",
        "reason": (
            "Memory checkpoint (fires at most every 30 min): if this session "
            "established durable new facts, user preferences, corrections, or "
            "project state not yet in memora, save them now with memory_create "
            "(typed + focus:/project: tags, metadata.name slug). If nothing "
            "qualifies — most turns — just finish; do not mention this check."
        ),
    }))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
