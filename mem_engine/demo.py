#!/usr/bin/env python3
"""End-to-end demo of mem_engine against a SCRATCH store (no live memora touched).

    python3 mem_engine/demo.py          # deterministic, MockAdaptor, no LLM
    python3 mem_engine/demo.py --real   # also run the real claude -p adaptor (sandboxed)

Seeds two "sessions" of observations, lets time pass so durable one-offs dwell
past the promotion threshold, consolidates them into atomic leaves, and shows
thin-pointer recall. Everything lives under a temp dir.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mem_engine.adaptor import ClaudeAdaptor, MockAdaptor  # noqa: E402
from mem_engine.embedder import HashingEmbedder  # noqa: E402
from mem_engine.engine import Engine, EngineConfig  # noqa: E402
from mem_engine.shortterm import WorkingMemory  # noqa: E402
from mem_engine.stores import SqliteLongTermStore  # noqa: E402


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


SESSION_1 = [
    "Perseus DDP training hangs on the workstation when using two ranks and cuSPARSE SpMM",
    "The Evandor PSU board EVA-PSU-100 uses a buck converter; the CMB was renamed from AFE",
    "From now on always write in UK English and never use em dashes",
    "Remember to keep memora backups local and never push them to OneDrive",
]
SESSION_2 = [
    "Perseus DDP training hangs on the workstation when using two ranks and cuSPARSE SpMM",
    "The Evandor PSU board EVA-PSU-100 uses a buck converter; the CMB was renamed from AFE",
    "what is the weather like today",  # ephemeral: no recurrence, no durable cue
]


def banner(t):
    print("\n" + "=" * 74 + f"\n{t}\n" + "=" * 74)


def run(adaptor, label):
    tmp = tempfile.mkdtemp(prefix="mem_engine_demo_")
    emb = HashingEmbedder()
    clk = Clock()
    wm = WorkingMemory(os.path.join(tmp, "st.db"), embedder=emb, clock=clk)
    lt = SqliteLongTermStore(os.path.join(tmp, "lt.db"), embedder=emb)
    eng = Engine(wm, lt, adaptor, EngineConfig(enabled=True, min_score=0.0))

    banner(f"{label} — observe two sessions into working memory")
    for t in SESSION_1:
        eng.observe(t, session="s1")
    clk.advance(13 * 3600)  # time passes; durable one-offs cross the 12h dwell
    for t in SESSION_2:
        eng.observe(t, session="s2")
    print(json.dumps(eng.status(), indent=2))

    banner(f"{label} — consolidate (promote durable episodes -> atomic leaves)")
    print(json.dumps(eng.consolidate(), indent=2))

    banner(f"{label} — resulting long-term atomic leaves")
    rows = lt._conn.execute("select id,name,content,tags from memories order by id").fetchall()
    if not rows:
        print("  (none)")
    for mid, name, content, tags in rows:
        print(f"\n[{mid}] {name}   tags={tags}   ({len(content)} chars)")
        print("    " + content.replace("\n", "\n    "))

    banner(f"{label} — recall (thin pointers; caller fetches full leaf on demand)")
    for q in ["why does perseus training hang", "how should I write things",
              "evandor psu board power"]:
        out = eng.recall(q)
        print(f"\nPROMPT: {q}")
        print(out["context"] or "  (no hits above threshold)")
    wm.close()


def main():
    run(MockAdaptor(), "MOCK (deterministic, no LLM)")
    if "--real" in sys.argv:
        import shutil
        if shutil.which("claude"):
            run(ClaudeAdaptor(), "REAL (claude -p, sandboxed, no memora tools)")
        else:
            print("\n[--real requested but `claude` not found on PATH; skipped]")


if __name__ == "__main__":
    main()
