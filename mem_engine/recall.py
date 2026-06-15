"""Recall: prompt -> thin pointers into the hub-and-spoke graph.

Returns id + slug + score + first-sentence preview, NOT full bodies. The caller
fetches a leaf with memory_get only if it actually needs it — so auto-recall
injects a few hundred bytes of pointers rather than kilobytes of memory content
(the efficiency posture the whole restructure is built around). An optional
reranker can reorder candidates; it is off by default to keep recall LLM-free.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

MIN_PROMPT_LEN = 12


def default_min_score(embedder_name: str) -> float:
    """The hashing fallback yields lower cosines than bge — floor accordingly,
    so auto-recall doesn't silently return nothing when no model is installed."""
    return 0.15 if embedder_name == "hashing" else 0.62


@dataclass
class Pointer:
    id: int
    name: Optional[str]
    score: float
    tags: List[str]
    preview: str


def recall_for_prompt(prompt: str, longterm, *, top_k: int = 4,
                      min_score: float = 0.62,
                      reranker: Optional[Callable[[str, List[Dict]], List[Dict]]] = None
                      ) -> List[Pointer]:
    prompt = (prompt or "").strip()
    if len(prompt) < MIN_PROMPT_LEN:
        return []
    fetch_k = top_k * 3 if reranker else top_k
    hits = longterm.search(prompt, top_k=fetch_k, min_score=min_score)
    if reranker and hits:
        hits = reranker(prompt, hits)
    hits = hits[:top_k]
    return [Pointer(h["id"], h.get("name"), h["score"], h.get("tags", []),
                    h.get("preview") or "") for h in hits]


def format_pointers(pointers: List[Pointer]) -> str:
    if not pointers:
        return ""
    lines = []
    for p in pointers:
        tags = ",".join(p.tags or [])
        lines.append(f"- [[{p.name}]] (id {p.id} | score {p.score} | {tags}) — {p.preview}")
    return (
        "Relevant memora leaves (thin pointers — fetch full content with memory_get "
        "only if needed; background context, may be stale, ignore if not applicable):\n"
        + "\n".join(lines)
    )
