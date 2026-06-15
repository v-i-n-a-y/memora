#!/usr/bin/env python3
"""Continuous consolidation worker — promote ready episodes on a loop.

Runs as a sidecar next to the MCP server (same image, command
``python -m mem_engine.worker``). It builds ONE engine from the environment
(same config as the server: adaptor, long-term store, AUTOWRITE) and calls
``consolidate()`` every ``MEM_ENGINE_CONSOLIDATE_INTERVAL`` seconds.

consolidate() is cheap when nothing is ready — it's a single indexed query for
episodes past the recurrence/dwell gate — and only invokes the LLM when there is
genuinely something to promote. So this can poll frequently ("constantly") at
negligible cost, replacing the once-a-day cron. The worker shares the
working-memory and long-term stores with the server process via their DB files
(WAL mode keeps concurrent access clean).
"""
from __future__ import annotations

import json
import os
import sys
import time

try:
    from filelock import FileLock, Timeout  # a memora dependency; present in the image
except Exception:  # degrade gracefully if filelock is unavailable
    FileLock = None
    Timeout = Exception

from .mcp_tools import get_engine


def _log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def main() -> None:
    interval = float(os.environ.get("MEM_ENGINE_CONSOLIDATE_INTERVAL", "60"))
    # Singleton: hold a file lock for the process lifetime so a second worker
    # (e.g. relaunched by the keep-alive cron) exits immediately instead of
    # racing on the stores. Acquired before the engine/embedder load, so an
    # extra instance costs almost nothing.
    lock_path = os.environ.get("MEM_ENGINE_LOCK", "/data/memora/mem_engine_worker.lock")
    if FileLock is not None:
        _singleton = FileLock(lock_path)  # noqa: F841 (held for process lifetime)
        try:
            _singleton.acquire(timeout=0)
        except Timeout:
            return  # another worker already holds the lock; exit quietly
    eng = get_engine()
    _log(f"[mem_engine.worker] started; interval={interval}s enabled={eng.config.enabled} "
         f"adaptor={getattr(eng.adaptor, 'name', '?')} longterm={eng.longterm.count()}")
    while True:
        try:
            res = eng.consolidate()
            # Only log rounds that actually had work, to keep the log quiet.
            if res.get("ready"):
                _log(f"[mem_engine.worker] {json.dumps(res)}")
        except Exception as exc:  # never let one bad round kill the loop
            _log(f"[mem_engine.worker] error: {exc!r}")
        time.sleep(interval)


if __name__ == "__main__":
    main()
