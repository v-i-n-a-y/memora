"""Minimal CLI for clmux daemon integration.

Usage:
    python3 -m memora.cli search "query" [--top-k 7] [--tags tag1,tag2]
    python3 -m memora.cli health
"""
from __future__ import annotations

import json
import sys


def cmd_health() -> None:
    from .storage import connect, get_statistics

    try:
        conn = connect()
        try:
            stats = get_statistics(conn)
            count = stats.get("total_memories", 0)
        finally:
            conn.close()
        json.dump({"status": "ok", "memory_count": count}, sys.stdout)
    except Exception as exc:
        json.dump({"status": "error", "message": str(exc)}, sys.stdout)
        sys.exit(1)


def cmd_search(query: str, top_k: int = 7, tags_any: list[str] | None = None) -> None:
    from .storage import connect, hybrid_search

    conn = connect()
    try:
        results = hybrid_search(
            conn,
            query,
            semantic_weight=0.6,
            top_k=top_k,
            min_score=0.0,
            tags_any=tags_any or None,
        )
    finally:
        conn.close()

    # Compact preview: strip full content, keep preview
    for entry in results:
        if "memory" in entry:
            mem = entry["memory"]
            full = mem.pop("content", "") or ""
            mem["content_preview"] = full[:300] + "\u2026" if len(full) > 300 else full

    json.dump({"count": len(results), "results": results}, sys.stdout)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("usage: python3 -m memora.cli {health|search} ...", file=sys.stderr)
        sys.exit(1)

    cmd = args[0]
    if cmd == "health":
        cmd_health()
    elif cmd == "search":
        if len(args) < 2:
            print("usage: python3 -m memora.cli search QUERY [--top-k N] [--tags t1,t2]", file=sys.stderr)
            sys.exit(1)
        query = args[1]
        top_k = 7
        tags_any = None
        i = 2
        while i < len(args):
            if args[i] == "--top-k" and i + 1 < len(args):
                top_k = int(args[i + 1])
                i += 2
            elif args[i] == "--tags" and i + 1 < len(args):
                tags_any = args[i + 1].split(",")
                i += 2
            else:
                i += 1
        cmd_search(query, top_k=top_k, tags_any=tags_any)
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
