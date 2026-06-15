"""LLM ingest adaptor: raw working-memory episodes -> atomic LEAVES.

This is the "intelligence at the door". Instead of every caller remembering the
house style, the adaptor normalises durable observations into well-formed atomic
leaves (naming, type/section, hub link, size). Two implementations:

  - MockAdaptor   : deterministic, rule-based. No LLM. Powers hermetic tests and
                    the offline demo, and is a safe default fallback.
  - ClaudeAdaptor : real distillation via `claude -p`, run SANDBOXED with no
                    memora tools. It returns structured JSON only; the engine
                    validates + persists, so the model can never write to the
                    store directly (a deliberate safety upgrade over the archive).

Both return a DistillResult(leaves, outcomes) where outcomes maps every input
episode id to (status, memory_name) with status in {stored, duplicate, ephemeral}.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .schema import (GENERIC_TAGS, SCOPE_SECTION, VALID_SECTIONS, VALID_TYPES,
                     Leaf)
from .shortterm import Episode

# ---------------------------------------------------------------------------

@dataclass
class DistillResult:
    leaves: List[Leaf] = field(default_factory=list)
    outcomes: Dict[int, Tuple[str, Optional[str]]] = field(default_factory=dict)


_STOPWORDS = {
    "the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "is",
    "are", "be", "this", "that", "it", "i", "you", "we", "they", "my", "our",
    "should", "would", "can", "will", "use", "using", "used", "please", "make",
    "sure", "always", "never", "prefer", "from", "now", "do", "not", "dont",
    "want", "need", "when", "whenever", "by", "default", "instead", "stop",
}

# scope keyword -> scope prefix (drives slug prefix, section, tags, hub link)
_SCOPE_KEYWORDS = {
    "perseus": "perseus", "jax": "perseus", "flax": "perseus", "nnx": "perseus",
    "gnn": "perseus", "checkpoint": "perseus",
    "thesis": "phd", "phd": "phd", "viva": "phd", "doctorate": "phd", "osiris": "phd",
    "evandor": "eva", "psu": "eva", "cmb": "eva", "cmu": "eva", "kicad": "eva",
    "pcb": "eva", "atout": "eva", "tomoflow": "eva",
    "astrodynamic": "astro", "swiss": "astro", "satellite": "astro", "stac": "astro",
}

_HUB_FOR_SCOPE = {
    "perseus": "perseus-hub", "phd": "phd-working-tree", "eva": "evandor-working-tree",
    "astro": "astrodynamic-overview",
}

_FEEDBACK_CUES = ("prefer", "always", "never", "from now on", "don't", "do not",
                  "instead of", "make sure", "going forward", "by default",
                  "each time", "whenever", "stop ", "no longer")


def _slug_tokens(text: str, limit: int = 4) -> List[str]:
    toks = re.findall(r"[a-z0-9]+", text.lower())
    out = [t for t in toks if t not in _STOPWORDS and len(t) > 2]
    seen, uniq = set(), []
    for t in out:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
        if len(uniq) >= limit:
            break
    return uniq or (toks[:limit] if toks else ["note"])


def _detect_scope(text: str) -> Optional[str]:
    low = text.lower()
    for kw, scope in _SCOPE_KEYWORDS.items():
        if kw in low:
            return scope
    return None


class MockAdaptor:
    """Deterministic rule-based distiller. No LLM; stable output for tests."""

    name = "mock"

    def distill(self, episodes: List[Episode]) -> DistillResult:
        res = DistillResult()
        # default: every episode ephemeral unless it yields a leaf
        for ep in episodes:
            res.outcomes[ep.id] = ("ephemeral", None)

        for ep in episodes:
            text = ep.text.strip()
            low = text.lower()
            scope = _detect_scope(text)
            is_feedback = any(c in low for c in _FEEDBACK_CUES) or bool(ep.durable)

            mtype = "feedback" if is_feedback else "project"
            section = SCOPE_SECTION.get(scope, "working-style") if scope else (
                "working-style" if is_feedback else "phd")
            if section not in VALID_SECTIONS:
                section = "working-style"

            toks = _slug_tokens(text)
            if scope:
                toks = [t for t in toks if t != scope] or toks
            slug_body = "-".join(toks)
            name = f"{scope}-{slug_body}" if scope else slug_body
            name = re.sub(r"-+", "-", name).strip("-")[:60].rstrip("-")

            links: List[str] = []
            tags: List[str] = []
            if scope and scope in _HUB_FOR_SCOPE:
                links.append(_HUB_FOR_SCOPE[scope])
            else:
                links.append("memora-usage-conventions")
            sec_focus = {"evandor": "focus:evandor", "phd": "focus:phd",
                         "astrodynamic": "focus:astrodynamic"}.get(section)
            if sec_focus:
                tags.append(sec_focus)
            if scope == "perseus":
                tags.append("project:perseus")

            summary = re.sub(r"\s+", " ", text)
            if len(summary) > 220:
                summary = summary[:217].rstrip() + "..."
            if mtype == "feedback":
                content = (f"{summary}\n\n**Why:** captured as a recurring/durable "
                           f"preference from working sessions.\n"
                           f"**How to apply:** honour this by default in relevant work.")
            else:
                content = summary

            leaf = Leaf(content=content, name=name, type=mtype, section=section,
                        tags=[t for t in tags if t not in GENERIC_TAGS],
                        links=links, source_episode_ids=[ep.id])
            res.leaves.append(leaf)
            res.outcomes[ep.id] = ("stored", name)
        return res


class ClaudeAdaptor:
    """Real distiller via `claude -p`, sandboxed (no memora tools). Returns JSON."""

    name = "claude"

    def __init__(self, model: Optional[str] = None, timeout: int = 240,
                 claude_bin: str = "claude"):
        self.model = model
        self.timeout = timeout
        self.claude_bin = claude_bin

    @staticmethod
    def _rm(path):
        try:
            os.remove(path)
        except OSError:
            pass

    def _prompt(self, episodes: List[Episode]) -> str:
        payload = [{"id": e.id, "text": e.text, "seen": e.seen,
                    "age_hours": e.age_hours, "durable": bool(e.durable)}
                   for e in episodes]
        return PROMPT_TEMPLATE.replace("{{PAYLOAD}}", json.dumps(payload, ensure_ascii=False))

    def distill(self, episodes: List[Episode]) -> DistillResult:
        res = DistillResult(outcomes={e.id: ("ephemeral", None) for e in episodes})
        if not episodes:
            return res
        # Sandbox: an empty MCP config + --strict-mcp-config means NO MCP servers
        # load, so this subprocess physically cannot reach the live memora store.
        cfg_fd, cfg_path = tempfile.mkstemp(suffix=".json", prefix="mem_engine_nomcp_")
        os.write(cfg_fd, b'{"mcpServers": {}}')
        os.close(cfg_fd)
        cmd = [self.claude_bin, "-p", self._prompt(episodes),
               "--strict-mcp-config", "--mcp-config", cfg_path,
               "--max-turns", "1"]
        if self.model:
            cmd += ["--model", self.model]
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout)
        except Exception:
            self._rm(cfg_path)
            return res  # episodes retained for retry
        self._rm(cfg_path)
        if out.returncode != 0:
            return res
        parsed = _extract_json(out.stdout or "")
        if not parsed:
            return res
        return _result_from_json(parsed, episodes)


PROMPT_TEMPLATE = """You distil persistent observations into atomic long-term memory "leaves" \
for a personal knowledge store. Output JSON ONLY — no prose, no tool calls.

INPUT — a JSON list of observations that have already persisted (recurred or dwelt):
{{PAYLOAD}}

Rules for each leaf:
- One atomic topic; first sentence is a standalone summary; <= 1000 chars.
- name: kebab-case "<scope>-<topic>"; scope is a project/focus prefix (perseus, eva, phd,
  astro) ONLY if the fact is specifically about that area; OMIT scope for cross-cutting
  facts (writing style, general tooling/preferences). Add a -YYYY-MM-DD suffix only for
  point-in-time events.
- type: one of user | feedback | project | reference (metadata, NOT a tag).
- section: one of evandor | phd | astrodynamic | pa | contacts | working-style.
  Cross-cutting preferences -> working-style.
- tags: add focus:evandor / focus:phd / focus:astrodynamic ONLY when the fact is
  specifically about that area's work; cross-cutting facts get NO focus tag; never add more
  than one focus tag unless it genuinely spans them; add project:<name> only for a specific
  sub-project (e.g. project:perseus, project:ect-cmu). When unsure, use [].
  There is NO focus:perseus — Perseus is project:perseus under focus:phd.
- links: choose ONLY from these real hubs: perseus-hub, ectcmu-hub, evandor-working-tree,
  phd-working-tree, astrodynamic-overview, contacts-convention, memora-usage-conventions.
  Include the single most relevant one; if none clearly fits, use [].
- feedback/project leaves: include a "Why:" line and a "How to apply:" line.
- Promote EVERY genuinely durable fact (a stable preference, a project fact/decision, an
  external reference). Mark "ephemeral" ONLY for transient task chatter. Merge closely
  related observations into ONE leaf.

Return a single JSON object (optionally inside a ```json fence):
{
  "leaves": [
    {"name": "...", "type": "...", "section": "...", "tags": ["..."],
     "links": ["..."], "content": "...", "source_episode_ids": [<id>, ...]}
  ],
  "outcomes": [
    {"id": <episode id>, "outcome": "stored|duplicate|ephemeral", "memory_name": "<slug or null>"}
  ]
}
Every input id must appear exactly once in "outcomes". If nothing is durable, return empty
"leaves" and mark every id "ephemeral"."""


def _extract_json(text: str) -> Optional[dict]:
    # Prefer a ```json fenced block; else the last balanced {...} object.
    fences = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = list(fences)
    if not candidates:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            candidates.append(text[start:end + 1])
    for c in reversed(candidates):
        try:
            obj = json.loads(c)
            if isinstance(obj, dict) and "leaves" in obj:
                return obj
        except Exception:
            continue
    return None


def _result_from_json(obj: dict, episodes: List[Episode]) -> DistillResult:
    valid_ids = {e.id for e in episodes}
    res = DistillResult(outcomes={e.id: ("ephemeral", None) for e in episodes})
    for raw in obj.get("leaves", []):
        try:
            leaf = Leaf(
                content=str(raw.get("content", "")).strip(),
                name=str(raw.get("name", "")).strip(),
                type=str(raw.get("type", "")).strip(),
                section=str(raw.get("section", "")).strip(),
                tags=[t for t in (raw.get("tags") or []) if t not in GENERIC_TAGS],
                links=list(raw.get("links") or []),
                source_episode_ids=[i for i in (raw.get("source_episode_ids") or [])
                                    if i in valid_ids],
            )
        except Exception:
            continue
        res.leaves.append(leaf)
    for o in obj.get("outcomes", []):
        try:
            eid = int(o["id"])
        except Exception:
            continue
        if eid in valid_ids:
            res.outcomes[eid] = (o.get("outcome") or "ephemeral", o.get("memory_name"))
    return res


class OpenAIAdaptor:
    """Real distiller via an OpenAI-compatible chat endpoint.

    Uses the openai SDK (already a memora dependency) so it runs INSIDE the
    deployed server against whatever LLM the server already has — e.g. ollama
    qwen3 via OPENAI_BASE_URL — with no extra CLI or credentials. Returns
    structured JSON only; the engine validates the schema and persists, so the
    model never writes to the store directly. Degrades to all-ephemeral (nothing
    promoted) if the endpoint or parse fails, so episodes are retried later.
    """

    name = "openai"

    def __init__(self, model=None, base_url=None, api_key=None, timeout=240):
        self.model = (model or os.environ.get("MEM_ENGINE_LLM_MODEL")
                      or os.environ.get("MEMORA_LLM_MODEL") or "gpt-4o-mini")
        self.base_url = base_url or os.environ.get("OPENAI_BASE_URL")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY") or "not-needed"
        self.timeout = timeout

    def distill(self, episodes: List[Episode]) -> DistillResult:
        res = DistillResult(outcomes={e.id: ("ephemeral", None) for e in episodes})
        if not episodes:
            return res
        payload = [{"id": e.id, "text": e.text, "seen": e.seen,
                    "age_hours": e.age_hours, "durable": bool(e.durable)} for e in episodes]
        prompt = PROMPT_TEMPLATE.replace("{{PAYLOAD}}", json.dumps(payload, ensure_ascii=False))
        try:
            from openai import OpenAI
            client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout)
            resp = client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            text = resp.choices[0].message.content or ""
        except Exception:
            return res  # endpoint unreachable -> retain episodes for retry
        parsed = _extract_json(text)
        if not parsed:
            return res
        return _result_from_json(parsed, episodes)
