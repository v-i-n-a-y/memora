"""Shared per-session dedup state for memora context-injection hooks.

A memory injected once in a Claude Code session is already in context, so it
shouldn't be injected again that session. SessionStart seeds the set with what
it shows; UserPromptSubmit reads it, skips already-seen ids, and adds whatever
it injects. Keyed by session_id.

Everything here is fail-open: the state is a token-efficiency optimisation, not
correctness, so any error just behaves as "nothing seen yet". Per-session files
self-prune after SEEN_TTL_SECONDS.
"""

import json
import time
from pathlib import Path

STATE_DIR = Path.home() / ".cache" / "memora" / "recall_seen"
SEEN_TTL_SECONDS = 2 * 24 * 3600  # drop dedup files from finished sessions


def _path(session_id):
    if not session_id:
        return None
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")
    return STATE_DIR / f"{safe}.json" if safe else None


def load_seen(session_id):
    """Set of memory ids already injected this session (empty on any miss)."""
    p = _path(session_id)
    if not p or not p.exists():
        return set()
    try:
        return set(json.loads(p.read_text()).get("ids", []))
    except Exception:
        return set()


def save_seen(session_id, ids):
    """Replace the seen-set for this session (used by SessionStart to seed)."""
    p = _path(session_id)
    if not p:
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"ids": sorted(i for i in ids if i is not None)}))
    except Exception:
        pass


def add_seen(session_id, ids):
    """Union new ids onto the existing seen-set for this session."""
    save_seen(session_id, load_seen(session_id) | {i for i in ids if i is not None})


def prune(now=None):
    """Delete dedup files older than SEEN_TTL_SECONDS."""
    try:
        now = now if now is not None else time.time()
        for f in STATE_DIR.glob("*.json"):
            if now - f.stat().st_mtime > SEEN_TTL_SECONDS:
                f.unlink()
    except Exception:
        pass
