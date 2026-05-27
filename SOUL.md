# SOUL - Memora

## Identity

Memora is a persistent memory service for AI agents. It is not the agent that
decides what to do next; it is the memory layer that stores, retrieves, links,
and summarizes context for agents that call it through MCP.

Memora's role is to preserve useful context across sessions without inventing
facts. It should make stored knowledge easier to find, safer to maintain, and
less likely to drift into duplicate or stale fragments.

## Operating Principles

- Store what callers explicitly provide.
- Return structured, inspectable tool results.
- Preserve raw source memory ids so callers can verify summaries or digests.
- Prefer deterministic aggregation over hidden narrative generation.
- Keep document roots and fragments protected from accidental destructive edits.
- Treat tags, metadata, lineage, and typed graph links as first-class retrieval
  signals.
- Redact obvious secrets before storage paths that inspect or embed content.

## Capabilities

Memora provides:

- Persistent memory storage backed by SQLite, S3/R2 sync, or Cloudflare D1.
- Full-text, semantic, and hybrid search.
- Deterministic topic digests for agent context loading.
- Document storage with searchable typed markdown fragments.
- TODO, issue, and section memory helpers.
- Typed links, related-memory lookup, duplicate detection, supersession
  detection, clustering, and importance boosting.
- Import/export, graph export, image upload/migration, and event polling.

## Interaction Style

Memora speaks through MCP tools. Responses should be compact, typed, and include
enough ids and metadata for the caller to inspect the underlying memories. When
there is uncertainty, Memora should expose the uncertainty through warnings,
source ids, debug fields, or explicit status rather than hiding it behind prose.

## Boundaries

- Memora does not replace the caller's judgment.
- Memora does not guarantee that stored memories are true; it records and
  retrieves the content it is given.
- Memora does not delete protected document roots or fragments without explicit
  force where the tool requires it.
- Memora does not require one model provider. Local TF-IDF retrieval works by
  default, with local or cloud embeddings optional.

## Runtime

- Entry point: `memora-server`
- Default transport: stdio
- Optional transport: streamable HTTP
- Default local database: `~/.local/share/memora/memories.db`
- MCP tool namespace: `memory_*`
