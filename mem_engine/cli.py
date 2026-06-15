#!/usr/bin/env python3
"""CLI for mem_engine — drive the engine from any shell or assistant.

    python3 -m mem_engine.cli status
    python3 -m mem_engine.cli observe "some durable fact" [--session S] [--cwd C] [--kind turn]
    python3 -m mem_engine.cli recall "a prompt"
    python3 -m mem_engine.cli consolidate

Uses the persistent engine under $MEM_ENGINE_HOME (default ~/.local/share/mem_engine):
SqliteLongTermStore + MockAdaptor. Promotion stays gated behind MEM_ENGINE_AUTOWRITE=1.
This is a STANDALONE scratch store, NOT your live memora server.
"""
from __future__ import annotations

import argparse
import json

from .mcp_tools import tool_consolidate, tool_observe, tool_recall, tool_status


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="mem_engine")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="engine + store stats")
    po = sub.add_parser("observe", help="record an observation into working memory")
    po.add_argument("text")
    po.add_argument("--session")
    po.add_argument("--cwd")
    po.add_argument("--kind", default="turn")
    pr = sub.add_parser("recall", help="thin pointers relevant to a prompt")
    pr.add_argument("prompt")
    sub.add_parser("consolidate", help="promote persistent episodes (gated)")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        out = tool_status()
    elif args.cmd == "observe":
        out = tool_observe(args.text, session=args.session, cwd=args.cwd, kind=args.kind)
    elif args.cmd == "recall":
        out = tool_recall(args.prompt)
    elif args.cmd == "consolidate":
        out = tool_consolidate()
    else:  # pragma: no cover
        ap.error("unknown command")
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
