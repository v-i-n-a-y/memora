"""Engine facade + config — ties working memory, long-term store, adaptor.

This is the single object an MCP server (or any client glue) holds. The three
verbs map onto the architecture:
    observe()      -> working memory (cheap, local, no LLM)
    consolidate()  -> gated promotion to long-term (LLM only when durable)
    recall()       -> thin pointers into the graph
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Dict, Optional

from .adaptor import MockAdaptor
from .embedder import get_embedder
from .promote import promote as _promote
from .recall import default_min_score, format_pointers, recall_for_prompt
from .shortterm import WorkingMemory
from .stores import InMemoryLongTermStore


@dataclass
class EngineConfig:
    top_k: int = 4
    min_score: float = 0.62
    dedup_threshold: float = 0.82
    enabled: bool = False  # promotion opt-in (AUTOWRITE_ON analog)


class Engine:
    def __init__(self, working: WorkingMemory, longterm, adaptor=None,
                 config: Optional[EngineConfig] = None,
                 on_log: Optional[Callable[[str], None]] = None):
        self.working = working
        self.longterm = longterm
        self.adaptor = adaptor or MockAdaptor()
        self.config = config or EngineConfig()
        self.on_log = on_log

    def observe(self, text: str, **kw) -> Dict:
        return self.working.observe(text, **kw)

    def recall(self, prompt: str, *, reranker=None) -> Dict:
        ptrs = recall_for_prompt(prompt, self.longterm, top_k=self.config.top_k,
                                 min_score=self.config.min_score, reranker=reranker)
        return {"pointers": [p.__dict__ for p in ptrs], "context": format_pointers(ptrs)}

    def consolidate(self) -> Dict:
        return _promote(self.working, self.longterm, self.adaptor,
                        enabled=self.config.enabled,
                        dedup_threshold=self.config.dedup_threshold, on_log=self.on_log)

    def status(self) -> Dict:
        return {"working": self.working.stats(),
                "longterm_count": self.longterm.count(),
                "promotion_enabled": self.config.enabled,
                "embedder": getattr(self.working.embedder, "name", "?"),
                "adaptor": getattr(self.adaptor, "name", "?")}


def build_default_engine(*, working_db: str = ":memory:", embedder_name: str = "auto",
                         longterm=None, adaptor=None, enabled: Optional[bool] = None) -> Engine:
    emb = get_embedder(embedder_name)
    wm = WorkingMemory(working_db, embedder=emb)
    lt = longterm if longterm is not None else InMemoryLongTermStore(embedder=emb)
    cfg = EngineConfig(min_score=default_min_score(emb.name))
    if enabled is None:
        cfg.enabled = os.environ.get("MEM_ENGINE_AUTOWRITE", "0").lower() in ("1", "true", "yes")
    else:
        cfg.enabled = enabled
    return Engine(wm, lt, adaptor=adaptor, config=cfg)
