# Optimize Memory Create/Update Performance

## Context

Memory create operations are slow (~5-25s on D1, ~2-3s locally) due to cascading cross-reference computation, synchronous OpenAI embedding API calls, redundant full-table scans, and unnecessary re-fetches. The update path already skips cross-refs ("too expensive for D1 HTTP API ~15 sec"), but create still pays the full cost.

**Goal:** Reduce single-create latency to ~2-4s on D1 and ~1-2s locally. Reduce batch-create latency proportionally.

---

## Phase 1: Drop cross-ref cascade

**Problem:** `_update_crossrefs()` (`storage.py:1665-1673`) computes cross-refs for the new memory, then **cascades** to recompute cross-refs for each of the 5 related memories. This is 5/6ths of the cross-ref cost — 5 extra full vector searches.

**Why safe to drop:** Duplicate detection and hierarchy suggestions only use the *new memory's* cross-refs (`server.py:427-428`, `465-466`). The update path already skips cross-refs entirely. Stale related-memory cross-refs get fixed by `memory_rebuild_crossrefs`.

**Semantic changes to document:** Dropping the cascade means stale reverse cross-refs affect:
- Clustering (`storage.py:1605`) — clusters may not reflect newest memory until rebuild
- Stats (`storage.py:2603`) — related counts may lag
- `memory_related(refresh=False)` (`server.py:275`) — older memories with existing (non-empty) stored refs will return stale related data until `refresh=True` is passed or a rebuild runs. `_get_related()` only auto-refreshes when a memory has **no** stored refs.
- `memory_related(refresh=True)` (`server.py:274`) — triggers a per-memory refresh, so unaffected

These are acceptable tradeoffs: the data is eventually consistent via `memory_rebuild_crossrefs`, and the same staleness already exists for updated memories. The `memory_related` default path (`refresh=False`) becomes eventually consistent — same as the update path already is.

**Changes:**
- `memora/storage.py:1671-1673` — Remove the cascade loop. Keep only the primary `_update_crossrefs_for_memory(conn, memory_id)` call.
- Do NOT create `_update_crossrefs_with_cascade()` — `rebuild_crossrefs()` (`storage.py:1676`) already iterates every memory directly and doesn't need a cascade wrapper.

**Savings:** ~4-15s on D1 (eliminates 5× full-table vector search).

---

## Phase 2: Lightweight vector search for cross-refs

**Problem:** `_search_by_vector()` (`storage.py:1194-1230`) calls `list_memories()` which does `SELECT *` and deserializes every row — but cross-ref computation only needs IDs and embeddings.

**Edge case — lazy embedding backfill:** The current `_search_by_vector()` lazy-computes embeddings for candidates that are missing them (`storage.py:1215-1221`). The lightweight search must preserve this behavior for legacy/imported memories without embeddings.

**Changes:**
- `memora/storage.py` — Add `_search_by_vector_ids_only()` that:
  1. Fetches `SELECT id, content, metadata, tags FROM memories` (need content/metadata/tags for lazy backfill fallback)
  2. Loads embeddings via `_get_embeddings_for_ids()`
  3. For candidates with missing embeddings: computes and stores them (preserving backfill)
  4. Computes cosine similarity, returns `[{id, score}]`
  - Key difference from `_search_by_vector()`: does NOT deserialize into full memory dicts, does NOT return `memory` key in results — just `{id, score}`
- `memora/storage.py:1269-1304` — `_update_crossrefs_for_memory()` uses the lightweight search.
- Ensure `add_memories()` upserts all embeddings (`storage.py:1821`) before any `_update_crossrefs()` call (`storage.py:1822`) — this is already the case in the current code order.

**Savings:** Smaller D1 response payload, less Python deserialization. Still preserves backfill for legacy records.

---

## Phase 3: Construct return value instead of re-fetching

**Problem:** `add_memory()` (`storage.py:1773`) calls `get_memory()` to re-read the just-inserted record (2 DB queries). `add_memories()` (`storage.py:1833`) does the same per memory.

**Key insight:** `update_memory()` already constructs its return locally (`storage.py:1929-1952`) to avoid D1 replica lag.

**Section-memory guard:** `_update_crossrefs()` skips section memories via `metadata.type == "section"` check (`storage.py:1669`). When calling `_update_crossrefs_for_memory()` directly from `add_memory()`, replicate the section guard inline before the call.

**Return value fidelity:** The constructed return must match `get_memory()` / `_serialise_row()` semantics (`storage.py:762`): expanded metadata (via `_present_metadata`), importance fields (`importance`, `importance_score`), and `related` list.

**Changes:**
- `memora/storage.py:1769-1773` — Add the section-type guard inline, call `_update_crossrefs_for_memory(conn, memory_id, vector=vector)` directly (passing the already-computed embedding from line 1767 to avoid an extra embedding lookup on D1), capture the related list, construct result dict matching `_serialise_row()` output shape.
- `memora/storage.py:1833` — Same pattern for `add_memories()`, passing the pre-computed vector from the embeddings list.

**Savings:** 2 fewer D1 HTTP requests per create (~200-600ms).

---

## Phase 4: Fix + optimize hierarchy path check

**Problem:** `server.py:389-391` calls `_list_memories(None, None, None, 0, ...)` to get hierarchy paths, but `_clamp_limit(0)` returns 1 (`storage.py:1128`). So it only checks **1 memory** — this is a correctness bug, not just a performance issue.

**Changes:**
- `memora/storage.py` — Add `get_hierarchy_paths(conn)`:
  - Query: `SELECT metadata FROM memories WHERE metadata IS NOT NULL` (lightweight, no content)
  - In Python: call `extract_hierarchy_path()` on each row's parsed metadata (preserving support for both `hierarchy.path` and top-level `section`/`subsection` formats from `hierarchy.py:38-56`)
  - Expand parent prefixes in Python (matching `get_existing_hierarchy_paths()` behavior at `hierarchy.py:119`)
  - Return sorted unique paths
- `memora/server.py:389-395` — Replace the broken `_list_memories(limit=0)` + `get_existing_hierarchy_paths()` call with a wrapped `_get_hierarchy_paths()`.

**Note:** Using `json_extract` SQL would miss `hierarchy.path` format. Keep extraction in Python via `extract_hierarchy_path()` but fetch only `metadata` column instead of full rows.

**Savings:** Fixes the bug (currently only checks 1 memory). On D1, fetches metadata-only instead of full rows.

---

## Phase 5: Batch metadata fetch for hierarchy suggestions

**Problem:** `suggest_hierarchy_from_similar()` (`server.py:466-469`) calls `_get_memory()` per related memory (up to 5), each opening a new connection with `ensure_schema()` check.

**Changes:**
- `memora/storage.py` — Add `get_memories_metadata_batch(conn, memory_ids)` that fetches metadata for multiple IDs in one query.
- `memora/hierarchy.py` — Modify `suggest_hierarchy_from_similar()` to accept pre-fetched metadata dict instead of a `get_memory_by_id` callback.
- `memora/server.py:465-469` — Batch-fetch metadata for related memories and pass to the modified hierarchy function.

**Savings:** 5× (connect + schema check + query + crossref query) → 1 SQL query. ~1-3s on D1.

---

## Phase 6: Batch OpenAI embeddings for `add_memories()`

**Problem:** `add_memories()` computes embeddings sequentially in a loop (`storage.py:1805`). Each OpenAI API call takes 1-2s.

**Changes:**
- `memora/embeddings.py` — Add `compute_embeddings_batch(texts, model)`:
  - For OpenAI: calls `client.embeddings.create(input=chunk)` with chunked input (max 2048 texts per call per OpenAI limits), preserves output order
  - Uses same text assembly path as `_get_embedding_text()` / `compute_embedding()` (`embeddings.py:18`, `embeddings.py:102`) to ensure identical payloads
  - **Error handling:** Preserve current failure semantics — OpenAI path falls back to TF-IDF on missing key, import failure, or API error (`embeddings.py:72`). If a batch chunk fails, fall back to per-item sequential computation (which individually falls back to TF-IDF). This ensures `memory_create_batch` is never less reliable than sequential creates.
  - Falls back to sequential for sentence-transformers/tfidf backends
- `memora/storage.py` `add_memories()` — Collect all embedding texts via `_get_embedding_text()`, call batch function once, map results back by index.

**Savings:** 10 memories batch: 10 API calls (10-20s) → 1 API call (1-2s).

---

## Phase 7: Fix D1 batch ID assignment (correctness)

**Problem:** `add_memories()` (`storage.py:1815-1817`) reconstructs inserted IDs from `last_insert_rowid()` assuming contiguous range. But D1's `executemany()` (`backends.py:931`) executes separate HTTP inserts, so under concurrent writers, IDs may not be contiguous.

**Changes:**
- `memora/storage.py` `add_memories()` — For D1 backend: insert rows individually and collect actual IDs from each `last_insert_rowid()` call. For local SQLite: keep the existing `executemany()` + range approach (safe under single-writer WAL mode).
- Detect backend type by checking if connection is a `D1Connection` instance.

---

## Implementation Order

| Phase | Impact | Effort | Risk |
|-------|--------|--------|------|
| 1. Drop cascade | Very High | ~15 min | Low |
| 2. Lightweight vector search | High (D1) | ~45 min | Low (preserves backfill) |
| 3. Construct return values | Medium | ~30 min | Low (match serialise_row) |
| 4. Fix hierarchy path check | Medium (bug fix) | ~30 min | Low |
| 5. Batch metadata fetch | Medium | ~45 min | Low |
| 6. Batch embeddings | High (batch) | ~45 min | Low (chunked, same text path) |
| 7. Fix D1 batch IDs | Low (correctness) | ~30 min | Low |

## Files to Modify

- `memora/storage.py` — Phases 1, 2, 3, 4, 5, 6, 7
- `memora/server.py` — Phases 4, 5
- `memora/embeddings.py` — Phase 6
- `memora/hierarchy.py` — Phase 5

## Verification

1. Run existing tests: `python -m pytest tests/`
2. Manual test: create a memory via MCP, verify response includes correct cross-refs
3. Manual test: create a batch of 3+ memories, verify all returned correctly
4. Manual test: update a memory, verify embedding recomputed
5. Verify hierarchy suggestions still appear in create response
6. Run `memory_rebuild_crossrefs` and verify full cascade still works
7. Compare timing before/after on both local SQLite and D1 backends

### New tests to add
- Batch of 3 similar memories: verify each returned record has stable, order-independent `related` results
- Missing candidate embeddings: verify Phase 2 lightweight search still backfills and includes them in cross-ref results
- Hierarchy path with parent prefixes: existing `docs/api`, verify `docs` appears as existing path
- Large-batch embedding: verify chunking preserves output order and text format matches single-create path
- Section-type memory create: verify no cross-refs computed (guard preserved after Phase 3 refactor)

## Review History

### v2 → v3
Addressed Codex v2 findings (no High issues):
- **Phase 1:** Expanded semantic changes to explicitly call out `memory_related(refresh=False)` path as eventually consistent — older memories with non-empty stored refs won't auto-refresh.
- **Phase 3:** Explicitly pass pre-computed `vector` to `_update_crossrefs_for_memory()` to avoid redundant embedding lookup on D1.
- **Phase 6:** Added error handling requirement — batch chunk failures fall back to per-item sequential (preserving existing TF-IDF fallback semantics).

### v1 → v2
Addressed Codex review findings:
- **Phase 1:** Added explicit documentation of semantic changes (clustering, stats, memory_related). Removed `_update_crossrefs_with_cascade()` proposal — `rebuild_crossrefs()` already handles this.
- **Phase 2:** Added lazy embedding backfill preservation. Fetch content/metadata/tags (not just IDs) to support backfill. Confirmed `add_memories()` upserts embeddings before crossref computation.
- **Phase 3:** Added section-memory guard replication. Added return value fidelity requirements (`_serialise_row()` semantics).
- **Phase 4:** Reframed from "optimization" to "bug fix + optimization". `_clamp_limit(0)=1` means current code only checks 1 memory. Kept extraction in Python via `extract_hierarchy_path()` to preserve both `hierarchy.path` and `section/subsection` formats plus parent prefix expansion.
- **Phase 6:** Added chunking (max 2048 per OpenAI call), explicit same-text-assembly-path requirement.
- **Phase 7 (new):** Added D1 batch ID correctness fix — `executemany()` on D1 doesn't guarantee contiguous IDs.
- **Verification:** Added 5 new test cases covering missing embeddings, section-type guard, hierarchy prefixes, batch ordering, and batch cross-ref stability.
