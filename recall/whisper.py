#!/usr/bin/env python3
"""SessionStart hook: 'whisper' the durable long-term anchors from memora into
context at session start, so the persistent picture is always present. This
complements the per-prompt UserPromptSubmit recall (which surfaces short-term,
query-relevant memories on demand).

Reads the memora SQLite DB directly (no embeddings, no daemon) so it is fast and
dependency-free. Never blocks or fails.
"""
import json
import os
import sqlite3
import sys

DB = os.path.expanduser("~/.local/share/memora/memories.db")
MAX_ITEMS = 10
LIMIT = 240


def first_sentence(text):
    t = " ".join((text or "").split())
    for sep in (". ", " — "):
        i = t.find(sep)
        if 40 <= i <= LIMIT:
            return t[: i + (1 if sep == ". " else 0)]
    return t[:LIMIT]


def main():
    if not os.path.exists(DB):
        return
    try:
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "select id, content, tags, metadata, access_count from memories"
        ).fetchall()
    except Exception:
        return

    enriched = []
    for r in rows:
        try:
            tags = json.loads(r["tags"] or "[]")
        except Exception:
            tags = []
        try:
            mtype = (json.loads(r["metadata"] or "{}") or {}).get("type")
        except Exception:
            mtype = None
        if "archive" in tags:
            continue  # never anchor on retired/archived content
        enriched.append((r, tags, mtype))

    picked, seen = [], set()
    # 1) orientation anchors (index-tagged) + the canonical user profile
    for r, tags, mtype in enriched:
        if ("index" in tags or mtype == "user") and r["id"] not in seen:
            picked.append(r)
            seen.add(r["id"])
    # 2) fill to MAX_ITEMS with the most-accessed memories
    for r, tags, mtype in sorted(enriched, key=lambda x: x[0]["access_count"] or 0, reverse=True):
        if len(picked) >= MAX_ITEMS:
            break
        if r["id"] not in seen:
            picked.append(r)
            seen.add(r["id"])

    picked = picked[:MAX_ITEMS]
    if not picked:
        return

    lines = [f"- [{r['id']}] {first_sentence(r['content'])}" for r in picked]
    ctx = (
        "Long-term memory (persistent anchors from memora — durable background "
        "context; verify specifics before relying on them):\n" + "\n".join(lines)
    )
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": ctx,
    }}))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
