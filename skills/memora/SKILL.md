# Memora — Persistent memory and knowledge management

Memora is an MCP memory server. Use it at session start to load context, when the user asks about past work or stored knowledge, and when saving important information for future sessions.

## When to invoke

- Session start: load relevant memories for the current task
- User asks about past work, decisions, or stored knowledge
- Saving important findings, decisions, architecture notes, or research
- User explicitly asks to remember, recall, or search memories

## Tool usage guidelines

### Retrieval — use `follow` for clean results

**Always use `follow="active"` when browsing or listing memories.** This excludes superseded memories, so you only see current information — not outdated versions.

```
memory_list(tags_any=["memora/todos"], follow="active")
memory_semantic_search(query="cloud backend", follow="active")
memory_hybrid_search(query="swap loss research", follow="active")
```

**Use `follow="latest"` when fetching a specific memory that may have been updated.** This resolves through supersession chains to the current version automatically.

```
memory_get(memory_id=157, follow="latest")  # returns #170 if 157 was superseded
memory_semantic_search(query="roadmap", follow="latest")  # deduplicates versions
```

**Use `follow="full_history"` to see the evolution of a topic.** Returns all versions in the supersession chain, root to leaf.

```
memory_get(memory_id=170, follow="full_history")  # returns history: [#157, #170]
```

**When to skip `follow`:** When you need raw results regardless of supersession state (e.g., debugging, auditing, or examining the full unfiltered store).

### Saving knowledge — use `memory_absorb`

**Prefer `memory_absorb` over `memory_create` when saving knowledge.** Absorb automatically checks for duplicates, supersedes outdated memories, links related ones, and consolidates related new facts into single richer memories.

**Write detailed, context-rich facts — not tiny one-liners.** Each fact should be a full sentence or short paragraph with enough context to be useful on its own. Related facts passed together are automatically merged into a single consolidated memory via LLM synthesis.

```
memory_absorb(
    facts=[
        "clmux v0.4.16.1 fixes hidden pane text leakage by restricting tmux allow-passthrough to only the visible TUI window instead of globally, preventing hidden reviewer and parking windows from leaking escape sequences",
        "clmux sidebar now filters out _reviewers and parking windows from list-panes queries, and the ! notification indicator persists across workspace switches until the user responds"
    ],
    source="manual",
    tags=["clmux", "bugfix"]
)
```

Absorb handles dedup and consolidation automatically:
- **Duplicate** → skipped (no new memory created)
- **Update** → creates new memory + supersedes the old one
- **Contradiction** → creates new memory + links with contradicts edge
- **Related** → creates new memory + links with related_to edge
- **New** → creates new memory (no matches found)
- **Consolidated** → related new facts merged into a single richer memory

Use `dry_run=True` to preview what absorb would do without writing.

**Use `memory_create` directly only for:**
- Raw/unprocessed content you want stored verbatim
- Structured entries (TODOs, issues, sections) via `memory_create_todo`, `memory_create_issue`, `memory_create_section`

### Updating memories

**Use `memory_update` only for corrections** (typos, metadata fixes) — not for evolving knowledge. For evolving knowledge, use `memory_absorb` — it handles supersession automatically.

### Linking

Use typed edges to express relationships:
- `supersedes` — new version replaces old (enables lineage walking)
- `contradicts` — conflicting information (flag for resolution)
- `implements` — concrete implementation of a plan/design
- `extends` — builds upon existing knowledge
- `references` — general reference/citation
- `related_to` — loose association

### Search strategy

1. **Start with `memory_semantic_search`** for conceptual queries (follow="active")
2. **Use `memory_hybrid_search`** when you need both keyword and semantic matching
3. **Use `memory_list`** with tag/metadata filters for structured browsing
4. **Use `fields` parameter** to reduce response size: `fields=["id", "content_preview", "tags"]`
5. **Use `content_mode="preview"`** (default) for scanning, `content_mode="full"` only when you need complete content

### Context efficiency

- Default `limit=20` on list. Use `limit=-1` only when you truly need everything.
- Use `fields` projection to fetch only what you need.
- Prefer `content_mode="preview"` (default) over full content for scanning.
- Use `memory_get` with specific IDs after finding relevant results via search.
