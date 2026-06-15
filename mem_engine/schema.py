"""Atomic-leaf schema + validator for the hub-and-spoke memora model.

A Leaf is one self-contained, single-topic memory. The validator enforces the
house style from the restructure runbook: kebab slug naming
(``<scope>-<topic>[-YYYY-MM-DD]``), a size ceiling, ``metadata.type``/``section``,
the generic-tag blocklist (tags memora silently namespaces), and an up-link to a
hub. The LLM adaptor proposes leaves; this module is the gate that keeps the
store atomic and linkable regardless of which model produced them.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

VALID_TYPES = {"user", "feedback", "project", "reference"}
VALID_SECTIONS = {"evandor", "phd", "astrodynamic", "pa", "contacts", "working-style"}
KNOWN_FOCUS = {"evandor", "phd", "astrodynamic"}  # the only valid focus:<area> values

# scope prefix -> the section it usually implies (sanity-check name vs section)
SCOPE_SECTION = {
    "eva": "evandor", "evandor": "evandor", "cmu": "evandor", "ectcmu": "evandor",
    "perseus": "phd", "phd": "phd", "rpic": "phd", "pptnet": "phd",
    "astro": "astrodynamic", "astrodynamic": "astrodynamic",
    "contact": "contacts", "pa": "pa", "memora": "working-style",
}

# tags memora's server silently namespaces (e.g. reference -> memora/reference),
# corrupting the tag vocabulary. Never emit these; the type lives in metadata.type.
GENERIC_TAGS = {
    "reference", "note", "plan", "task", "status", "analysis",
    "dataset", "experiment", "general", "model", "issue", "todo", "section",
}

SOFT_MAX_CHARS = 1200
HARD_MAX_CHARS = 2000

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_DATE_SUFFIX_RE = re.compile(r"-(\d{4})-(\d{2})-(\d{2})$")
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")

Problem = Tuple[str, str]  # (severity in {"error","warn"}, message)


@dataclass
class Leaf:
    content: str
    name: str
    type: str
    section: str
    tags: List[str] = field(default_factory=list)
    links: List[str] = field(default_factory=list)  # slugs this leaf points to
    source_episode_ids: List[int] = field(default_factory=list)

    def base_name(self) -> str:
        return _DATE_SUFFIX_RE.sub("", self.name or "")

    def scope(self) -> Optional[str]:
        base = self.base_name()
        return base.split("-", 1)[0] if "-" in base else (base or None)

    def first_sentence(self) -> str:
        s = (self.content or "").strip()
        period = s.find(". ")
        newline = s.find("\n")
        cands = [i for i in (period, newline) if i != -1]
        if not cands:
            return s
        cut = min(cands)
        return (s[:cut + 1] if cut == period else s[:cut]).strip()

    def render_content(self) -> str:
        """Body with a links footer appended idempotently."""
        body = (self.content or "").rstrip()
        existing = set(_WIKILINK_RE.findall(body))
        missing = [l for l in self.links if l not in existing]
        if missing:
            body += "\n\nRelated: " + " ".join(f"[[{l}]]" for l in missing)
        return body

    def to_memora(self) -> Dict[str, object]:
        """kwargs for memora.storage.add_memory(content=, metadata=, tags=)."""
        return {
            "content": self.render_content(),
            "metadata": {
                "name": self.name,
                "type": self.type,
                "section": self.section,
                "hierarchy": {"path": [self.section]},
                "source": "mem_engine",
            },
            "tags": [t for t in self.tags
                     if t not in GENERIC_TAGS
                     and not (t.startswith("focus:") and t.split(":", 1)[1] not in KNOWN_FOCUS)],
        }


def validate(leaf: Leaf) -> List[Problem]:
    """Return a list of (severity, message). 'error' blocks persistence."""
    problems: List[Problem] = []
    name = leaf.name or ""

    if not name:
        problems.append(("error", "missing name slug (unnamed = unlinkable)"))
    elif not _SLUG_RE.match(name):
        problems.append(("error", f"name '{name}' is not kebab-case"))

    m = _DATE_SUFFIX_RE.search(name)
    if m:
        try:
            datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            problems.append(("error", f"name '{name}' has an invalid date suffix"))

    if leaf.type not in VALID_TYPES:
        problems.append(("error", f"type '{leaf.type}' not in {sorted(VALID_TYPES)}"))
    if leaf.section not in VALID_SECTIONS:
        problems.append(("error", f"section '{leaf.section}' not in {sorted(VALID_SECTIONS)}"))

    scope = leaf.scope()
    if scope in SCOPE_SECTION and leaf.section and SCOPE_SECTION[scope] != leaf.section:
        problems.append(
            ("warn", f"scope '{scope}' usually implies section "
                     f"'{SCOPE_SECTION[scope]}', got '{leaf.section}'")
        )

    bad = sorted(set(leaf.tags) & GENERIC_TAGS)
    if bad:
        problems.append(
            ("error", f"generic tags {bad} are namespaced by memora — drop them "
                      "(type belongs in metadata.type)")
        )
    for t in leaf.tags:
        if t.startswith("focus:") and t.split(":", 1)[1] not in KNOWN_FOCUS:
            problems.append(("warn", f"unknown focus tag '{t}' — will be dropped on write "
                                     f"(known: {sorted(KNOWN_FOCUS)})"))

    n = len(leaf.content or "")
    if n > HARD_MAX_CHARS:
        problems.append(("error", f"content {n} chars exceeds hard cap {HARD_MAX_CHARS} — split"))
    elif n > SOFT_MAX_CHARS:
        problems.append(("warn", f"content {n} chars over soft target {SOFT_MAX_CHARS} — consider splitting"))

    if not leaf.first_sentence():
        problems.append(("error", "empty first sentence (must be a self-contained summary)"))

    is_hub = "index" in leaf.tags
    if not is_hub and not leaf.links:
        problems.append(("warn", "no [[links]] — link up to a hub and sideways to siblings"))

    return problems


def errors(leaf: Leaf) -> List[str]:
    return [msg for sev, msg in validate(leaf) if sev == "error"]


def is_valid(leaf: Leaf) -> bool:
    return not errors(leaf)
