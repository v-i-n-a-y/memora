"""Promotion (Tier 2): distil persistent episodes into atomic long-term leaves.

Pipeline: gate -> adaptor distils -> schema-validate each leaf -> dedup vs the
long-term store -> persist. Promotion is the only token-spending step and stays
OFF until explicitly enabled (the AUTOWRITE_ON opt-in), matching the archive's
safety posture. Every ready episode is marked with an outcome so it never loops
back into the promotion queue.
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

from . import schema
from .adaptor import DistillResult


def promote(working, longterm, adaptor, *, enabled: bool,
            dedup_threshold: float = 0.82,
            on_log: Optional[Callable[[str], None]] = None) -> Dict:
    log = on_log or (lambda m: None)
    ready = working.ready_for_promotion()
    summary: Dict = {"ready": len(ready), "written": 0, "duplicates": 0,
                     "ephemeral": 0, "invalid": 0, "gated": False, "leaves": []}
    if not ready:
        return summary
    if not enabled:
        log(f"promotion gated: {len(ready)} episode(s) ready, awaiting opt-in (enable to write)")
        summary["gated"] = True
        return summary

    result: DistillResult = adaptor.distill(ready)
    outcomes = dict(result.outcomes)  # eid -> (status, name)
    # Enforce the no-infinite-loop invariant engine-side: every ready episode
    # MUST get an outcome (so it advances), regardless of what the adaptor returned.
    for ep in ready:
        outcomes.setdefault(ep.id, ("ephemeral", None))

    for leaf in result.leaves:
        errs = schema.errors(leaf)
        if errs:
            log(f"skip invalid leaf '{leaf.name}': {errs[0]}")
            summary["invalid"] += 1
            for eid in leaf.source_episode_ids:
                outcomes[eid] = ("ephemeral", None)
            continue
        probe = leaf.first_sentence() or leaf.content
        hits = longterm.search(probe, top_k=1, min_score=0.0)
        if hits and hits[0]["score"] >= dedup_threshold:
            summary["duplicates"] += 1
            for eid in leaf.source_episode_ids:
                outcomes[eid] = ("duplicate", hits[0].get("name"))
            continue
        mid = longterm.add(leaf)
        summary["written"] += 1
        summary["leaves"].append({"id": mid, "name": leaf.name, "type": leaf.type,
                                  "section": leaf.section, "tags": leaf.tags})
        for eid in leaf.source_episode_ids:
            outcomes[eid] = ("stored", leaf.name)

    summary["ephemeral"] = sum(1 for status, _ in outcomes.values() if status == "ephemeral")
    working.mark_promoted(outcomes)
    working.expire()
    return summary
