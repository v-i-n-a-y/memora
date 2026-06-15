"""MCP-tool-shaped surface (JSON in / JSON out) so ANY MCP client can drive the
engine — not just Claude Code hooks. This is the v2 reframe: the intelligence
lives server-side in memora and is reachable by every assistant.

To wire into memora/server.py, register three thin tools that call these:
    memory_observe(text, session=None, cwd=None, kind="turn")  -> tool_observe
    memory_recall(prompt)                                       -> tool_recall
    memory_consolidate()                                        -> tool_consolidate
    memory_engine_status()                                      -> tool_status

A client (Claude Code, Codex, ...) then calls memory_observe at end of turn and
memory_recall at start of turn; a timer calls memory_consolidate. The default
engine here uses the MockAdaptor (no LLM); swap in ClaudeAdaptor for real
distillation. Promotion stays gated behind MEM_ENGINE_AUTOWRITE.
"""
from __future__ import annotations

import os
from typing import Optional

from .embedder import get_embedder
from .engine import Engine, EngineConfig
from .recall import default_min_score
from .shortterm import WorkingMemory
from .stores import SqliteLongTermStore

_ENGINE: Optional[Engine] = None


def _build_adaptor():
    """MEM_ENGINE_ADAPTOR: mock (default, no LLM) | openai (server's LLM) | claude (CLI)."""
    kind = os.environ.get("MEM_ENGINE_ADAPTOR", "mock").lower()
    if kind == "openai":
        from .adaptor import OpenAIAdaptor
        return OpenAIAdaptor()
    if kind == "claude":
        from .adaptor import ClaudeAdaptor
        return ClaudeAdaptor()
    from .adaptor import MockAdaptor
    return MockAdaptor()


def _build_longterm(emb, home):
    """MEM_ENGINE_LONGTERM: sqlite (default, scratch) | memora (the real graph)."""
    kind = os.environ.get("MEM_ENGINE_LONGTERM", "sqlite").lower()
    if kind == "memora":
        from .stores import MemoraLongTermStore
        return MemoraLongTermStore(db_path=os.environ.get("MEMORA_DB_PATH"))
    return SqliteLongTermStore(os.path.join(home, "longterm.db"), embedder=emb)


def get_engine() -> Engine:
    global _ENGINE
    if _ENGINE is None:
        home = os.environ.get("MEM_ENGINE_HOME",
                              os.path.expanduser("~/.local/share/mem_engine"))
        os.makedirs(home, exist_ok=True)
        emb = get_embedder(os.environ.get("MEM_ENGINE_EMBEDDER", "auto"))
        wm = WorkingMemory(os.path.join(home, "shortterm.db"), embedder=emb)
        lt = _build_longterm(emb, home)
        cfg = EngineConfig(
            min_score=default_min_score(emb.name),
            enabled=os.environ.get("MEM_ENGINE_AUTOWRITE", "0").lower() in ("1", "true", "yes"),
        )
        _ENGINE = Engine(wm, lt, adaptor=_build_adaptor(), config=cfg)
    return _ENGINE


def set_engine(engine: Engine) -> None:
    """Inject a preconfigured engine (used by tests and by server wiring)."""
    global _ENGINE
    _ENGINE = engine


def tool_observe(text: str, session: Optional[str] = None,
                 cwd: Optional[str] = None, kind: str = "turn") -> dict:
    return get_engine().observe(text, session=session, cwd=cwd, kind=kind)


def tool_recall(prompt: str) -> dict:
    return get_engine().recall(prompt)


def tool_consolidate() -> dict:
    return get_engine().consolidate()


def tool_status() -> dict:
    return get_engine().status()
