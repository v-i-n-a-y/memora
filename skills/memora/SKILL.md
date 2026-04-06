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

### Creating memories

- Use `memory_create` for new knowledge. Keep content atomic — one topic per memory.
- Use `response_mode="minimal"` to reduce token overhead on create.
- Set appropriate metadata: `type` (issue, todo, research, reference), `hierarchy.path`, `section`, `subsection`.
- Review `similar_memories` in the response — consider linking or superseding instead of duplicating.

### Updating and superseding

When information evolves, prefer **supersession over editing**:

1. Create the new memory with updated content
2. Link it: `memory_link(from_id=new, to_id=old, edge_type="supersedes")`

This preserves history. The old memory remains accessible via `follow="full_history"` but is automatically filtered out by `follow="active"` and resolved through by `follow="latest"`.

**Use `memory_update` only for corrections** (typos, metadata fixes) — not for evolving knowledge.

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
