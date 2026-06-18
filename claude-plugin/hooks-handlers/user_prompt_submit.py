#!/usr/bin/env python3
"""Memora UserPromptSubmit hook — inject memories relevant to the current prompt.

Complements session_start.py: where that orients the session once, this runs on
every turn and injects the memories most relevant to what the user just asked,
as hookSpecificOutput.additionalContext (the same mechanism Claude Code uses to
splice text into the model's context).

Token efficiency — per-turn injection has to stay cheap and non-redundant:
  * Session-level dedup (recall_state): a memory id is injected at most once per
    session, so recurring hits don't re-inject turn after turn. session_start.py
    seeds the set with what it already showed at startup.
  * Relevance gate + cap: a min_score floor and a small top-k.
  * Quiet: nothing is injected on short prompts, slash-commands, or when no
    memory clears the bar.

No network, no secrets: reads the local memora DB directly via `memora.storage`
(DB path resolved from the plugin .mcp.json, exactly like session_start.py).
Fail-open: any error prints `{}` and exits 0 — memory never blocks a prompt. A
one-line failure record is appended to LOG_PATH so silent breakage is visible.

Env knobs (all optional):
  MEMORA_RECALL_MIN_SCORE         default 0.02
  MEMORA_RECALL_TOP_K             default 4
  MEMORA_RECALL_MIN_PROMPT_CHARS  default 20
"""

import contextlib
import json
import os
import sys
import time
from pathlib import Path

import recall_state

MIN_PROMPT_CHARS = int(os.environ.get("MEMORA_RECALL_MIN_PROMPT_CHARS", "20"))
TOP_K = int(os.environ.get("MEMORA_RECALL_TOP_K", "4"))
MIN_SCORE = float(os.environ.get("MEMORA_RECALL_MIN_SCORE", "0.02"))
LOG_PATH = Path.home() / ".cache" / "memora" / "recall_hook.log"


def load_memora_env():
    """Hydrate MEMORA_* env from the plugin .mcp.json (same as session_start.py)."""
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    search_paths = []
    if plugin_root:
        search_paths.append(Path(plugin_root) / ".mcp.json")
    search_paths.extend([
        Path.home() / ".claude" / "settings.json",
        Path.home() / ".mcp.json",
        Path.cwd() / ".mcp.json",
    ])
    for mcp_path in search_paths:
        if mcp_path.exists():
            try:
                with open(mcp_path) as f:
                    config = json.load(f)
                env_vars = config.get("mcpServers", {}).get("memora", {}).get("env", {})
                for key, value in env_vars.items():
                    if key not in os.environ:
                        if isinstance(value, str) and value.startswith("~"):
                            value = os.path.expanduser(value)
                        os.environ[key] = str(value)
                return True
            except Exception:
                pass
    return False


def search_memora(query, top_k, min_score):
    """Hybrid (keyword + vector) search against the local memora DB."""
    try:
        from memora import storage
        conn = storage.connect()
        try:
            return storage.hybrid_search(
                conn, query=query, top_k=top_k, min_score=min_score
            )
        finally:
            conn.close()
    except Exception:
        return []


def format_memories(memories, seen, max_chars=1200):
    """Build the injection string from fresh (unseen) hits; return (text, ids)."""
    lines = ["## Recalled for this prompt (Memora — background context, may be stale)\n"]
    total = len(lines[0])
    injected = []
    for item in memories:
        memory = item.get("memory", item)
        mid = memory.get("id")
        if mid is None or mid in seen:
            continue
        content = (memory.get("content") or "").replace("\n", " ").strip()
        if len(content) > 160:
            content = content[:160] + "..."
        tags = memory.get("tags", [])
        tags_str = ", ".join(tags[:3]) if tags else ""
        entry = f"- [#{mid}] {content}"
        if tags_str:
            entry += f" ({tags_str})"
        entry += "\n"
        if total + len(entry) > max_chars:
            break
        lines.append(entry)
        total += len(entry)
        injected.append(mid)
        if len(injected) >= TOP_K:
            break
    if not injected:
        return "", []
    return "".join(lines), injected


def log_error(exc):
    """Record a failure so silent degradation is observable (fail-open hook)."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(LOG_PATH, "a") as fh:
            fh.write(f"{stamp}\t{type(exc).__name__}: {exc}\n")
    except Exception:
        pass


def main():
    # Claude Code parses this hook's stdout as JSON, so stdout MUST contain only
    # our final object. memora's storage layer can print to stdout (e.g. a
    # one-time embedding-rebuild notice), which would corrupt that contract — so
    # redirect any such chatter to stderr and write the JSON to the real stdout.
    real_stdout = sys.stdout
    result = {}
    try:
        raw = sys.stdin.read()
        with contextlib.redirect_stdout(sys.stderr):
            load_memora_env()
            data = json.loads(raw) if raw.strip() else {}
            prompt = (data.get("prompt") or "").strip()
            session_id = data.get("session_id")

            if len(prompt) >= MIN_PROMPT_CHARS and not prompt.startswith("/"):
                # Over-fetch a little so dedup still leaves up to TOP_K fresh hits.
                memories = search_memora(prompt, top_k=TOP_K + 8, min_score=MIN_SCORE)
                seen = recall_state.load_seen(session_id)
                context, injected = format_memories(memories, seen)
                if context and injected:
                    recall_state.add_seen(session_id, injected)
                    result = {
                        "hookSpecificOutput": {
                            "hookEventName": "UserPromptSubmit",
                            "additionalContext": context,
                        },
                        "suppressOutput": True,
                    }
    except Exception as exc:
        log_error(exc)
        result = {}
    real_stdout.write(json.dumps(result))
    sys.exit(0)


if __name__ == "__main__":
    main()
