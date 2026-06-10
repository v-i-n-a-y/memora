#!/usr/bin/env python3
"""Background consolidator (the mem0-style "add" step).

Drains captured (user, assistant) turns, asks a headless `claude -p` to extract
durable facts, and — in SHADOW MODE — writes the candidates to an inbox file for
review instead of committing straight to memora. Once the extraction quality is
trusted, flip SHADOW=False (or have the nightly dream ingest the inbox) to
auto-commit via the memora MCP.

Launched in the background by capture.py (the Stop hook) once enough turns have
accumulated. Self-locking so only one runs at a time. Standalone-runnable too.
"""
import json
import os
import subprocess
import time

_HDIR = os.path.dirname(os.path.abspath(__file__))
QUEUE = os.path.join(_HDIR, "capture_queue.jsonl")
BUF = os.path.join(_HDIR, "capture_queue.processing.jsonl")
INBOX = os.path.join(_HDIR, "extract_inbox.jsonl")
ARCHIVE = os.path.join(_HDIR, "capture_queue.processed.jsonl")
RUNLOG = os.path.join(_HDIR, "consolidate_runs.log")
LOCK = os.path.join(_HDIR, "consolidate.lock")

CONF_MIN = 0.6
MAX_TURNS_PER_RUN = 40
SHADOW = True  # write candidates to inbox for review; do not auto-commit to memora

PROMPT = """You extract durable memories from Claude Code session turns for Vinay.

Below are recent (USER, ASSISTANT) turns. Extract ONLY facts worth remembering \
across future sessions: stable user preferences, corrections/feedback on how to \
work, project facts or decisions, or external references. IGNORE ephemeral task \
state, one-off details, and Claude's own working narration.

For each fact output one JSON object:
- "type": user | feedback | project | reference
- "name": short kebab-case slug
- "tags": array; add focus:evandor / focus:astrodynamic / focus:phd when relevant, \
and project:<name> for a sub-project. Do NOT add generic tags like "reference"/"note".
- "content": self-contained; first sentence is a summary. UK English, no em dashes.
- "confidence": 0.0-1.0 that this is genuinely durable and worth saving
- "reason": one short clause

Output ONLY a JSON array (it may be empty). No prose, no code fences.

TURNS:
"""


def log(msg):
    try:
        with open(RUNLOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def main():
    if os.path.exists(LOCK):
        return
    if not os.path.exists(QUEUE) or os.path.getsize(QUEUE) == 0:
        return
    try:
        open(LOCK, "w").close()
    except Exception:
        return
    try:
        # rotate queue into a processing buffer so new turns keep accumulating
        try:
            with open(QUEUE) as f:
                lines = [l for l in f if l.strip()]
            with open(BUF, "a") as f:
                f.writelines(lines)
            open(QUEUE, "w").close()
        except Exception as e:
            log(f"rotate failed: {e}")
            return

        try:
            with open(BUF) as f:
                turns = [json.loads(l) for l in f if l.strip()]
        except Exception as e:
            log(f"read buffer failed: {e}")
            return
        if not turns:
            return

        recent = turns[-MAX_TURNS_PER_RUN:]
        blob = "\n\n".join(
            f"[{t.get('ts')}] USER: {t.get('user', '')}\nASSISTANT: {t.get('assistant', '')}"
            for t in recent
        )

        try:
            out = subprocess.run(
                ["claude", "-p", PROMPT + blob, "--max-turns", "1"],
                capture_output=True, text=True, timeout=240,
                env={**os.environ, "CLAUDE_DREAM_RUN": "1"},
            )
        except Exception as e:
            log(f"claude -p failed: {e}; buffer retained for retry")
            return

        raw = (out.stdout or "").strip()
        start, end = raw.find("["), raw.rfind("]")
        if start == -1 or end == -1 or end <= start:
            log(f"no JSON array in output; buffer retained. head={raw[:120]!r}")
            return
        try:
            cands = json.loads(raw[start:end + 1])
        except Exception as e:
            log(f"JSON parse failed: {e}; buffer retained")
            return

        kept = [
            c for c in cands
            if isinstance(c, dict) and float(c.get("confidence", 0) or 0) >= CONF_MIN
        ]

        if SHADOW:
            try:
                with open(INBOX, "a") as f:
                    for c in kept:
                        c["_captured_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                        f.write(json.dumps(c, ensure_ascii=False) + "\n")
            except Exception as e:
                log(f"write inbox failed: {e}; buffer retained")
                return

        # success: archive the processed buffer
        try:
            with open(ARCHIVE, "a") as a, open(BUF) as b:
                a.write(b.read())
            os.remove(BUF)
        except Exception as e:
            log(f"archive failed: {e}")
        log(f"processed {len(recent)} turns -> {len(kept)}/{len(cands)} candidate(s) "
            f"to {'inbox (shadow)' if SHADOW else 'memora'}")
    finally:
        try:
            os.remove(LOCK)
        except Exception:
            pass


if __name__ == "__main__":
    main()
