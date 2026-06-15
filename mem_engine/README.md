# mem_engine — working-memory + atomic-leaf promotion engine (v2)

A server-side memory engine for memora that turns raw observations into a clean,
atomic, hub-and-spoke long-term store — and serves thin, cheap recall. It is the
v2 of the retired `archive/recall-subsystem`, with three deliberate changes:

1. **Server-side, assistant-agnostic.** The logic is plain Python exposed as
   MCP-tool-shaped functions, not Claude Code hooks — so Claude Code, Codex, or
   any MCP client can drive it (the old layer only worked as CC hooks, the most
   likely reason it didn't stick).
2. **The LLM is an *adaptor*, not a writer.** Promotion's `claude -p` runs
   **sandboxed with no MCP tools** and only *returns structured leaves as JSON*;
   the engine validates the house-style schema and persists. The model can never
   write to the store directly (the archive handed it live `memory_create`).
3. **Atomic by construction.** Every promoted memory is a schema-validated
   *leaf* (kebab slug, ≤~1.2K chars, `metadata.type`/`section`, hub up-link) —
   the data model from the restructure runbook, enforced at the door.

## Architecture

```
  observe(turn/fact) ─► WORKING MEMORY (shortterm.db)        Tier 1: local, no LLM
                         • local embed, dedup (cos≥0.85)
                         • recurrence count, durable cues
                         • TTL + hard age cap
                              │  ready: seen≥2  OR  durable & dwelt≥12h
                              ▼
  consolidate() ────►  PROMOTION  (gated: off unless enabled)  Tier 2: LLM, rare
                         • adaptor distils episodes ─► LEAVES (JSON)
                         • schema-validate each leaf
                         • dedup vs long-term (cos≥0.82)
                         • persist
                              ▼
                       LONG-TERM STORE (the hub-and-spoke graph)
                              ▲
  recall_for_prompt() ──┘   thin pointers (id + slug + score + 1-line preview);
                            caller fetches full leaf with memory_get only if needed
```

## Components

| File | Role |
|------|------|
| `embedder.py` | `HashingEmbedder` (stdlib+numpy fallback) / `SentenceTransformerEmbedder` (bge, optional) |
| `schema.py` | `Leaf` dataclass + `validate()` — slug/date/size/tag/section house-style gate |
| `shortterm.py` | `WorkingMemory` — ingest, dedup, recurrence, durable cues, TTL + hard cap |
| `adaptor.py` | `MockAdaptor` (deterministic) / `ClaudeAdaptor` (sandboxed `claude -p`, returns JSON) |
| `stores.py` | `InMemoryLongTermStore` / `SqliteLongTermStore` / `MemoraLongTermStore` (guarded) |
| `promote.py` | gate → distil → validate → dedup → persist; guarantees every episode advances |
| `recall.py` | `recall_for_prompt` → thin `Pointer`s; embedder-aware `min_score` |
| `engine.py` | `Engine` facade (observe / consolidate / recall / status) + config |
| `mcp_tools.py` | `tool_observe/recall/consolidate/status` — the surface to register in memora |
| `cli.py` | `python3 -m mem_engine.cli {status,observe,recall,consolidate}` |
| `demo.py` | end-to-end demo on a scratch store (`--real` uses the live `claude -p` adaptor) |
| `tests/` | 18 hermetic tests (stdlib+numpy only; no network, no memora, no live store) |

## Quickstart

```bash
python3 -m unittest mem_engine.tests.test_engine -v   # 18 tests, hermetic
python3 mem_engine/demo.py                             # deterministic mock loop
python3 mem_engine/demo.py --real                      # also runs real claude -p (sandboxed)
```

## Wiring into memora (server-side, opt-in)

Register three thin tools in `memora/server.py` that call `mem_engine.mcp_tools`:

```python
from mem_engine import mcp_tools

@mcp.tool()
def memory_observe(text: str, session: str = None, cwd: str = None, kind: str = "turn") -> dict:
    return mcp_tools.tool_observe(text, session, cwd, kind)

@mcp.tool()
def memory_recall(prompt: str) -> dict:
    return mcp_tools.tool_recall(prompt)

@mcp.tool()
def memory_consolidate() -> dict:        # call on a timer / at session end
    return mcp_tools.tool_consolidate()
```

A client then calls `memory_observe` at end of turn and `memory_recall` at start of
turn; a scheduler calls `memory_consolidate`. To use the real distiller instead of
the mock, build the engine with `ClaudeAdaptor()` and inject it via
`mcp_tools.set_engine(...)`.

## Configuration (env vars)

| Var | Default | Meaning |
|-----|---------|---------|
| `MEM_ENGINE_HOME` | `~/.local/share/mem_engine` | where the scratch shortterm/longterm sqlite live |
| `MEM_ENGINE_AUTOWRITE` | `0` | **promotion opt-in** — must be `1` before anything is written long-term |
| `MEM_ENGINE_EMBEDDER` | `auto` | `auto` (bge if installed else hashing), `hashing`, or `sentence-transformers` |

Tunable thresholds (constructor args, defaults match the proven archive): dedup
cosine 0.85, promote on `seen≥2` or durable+12h, TTL 14d, hard cap 60d, recall
`top_k` 4, `min_score` 0.62 (bge) / 0.15 (hashing).

## Safety guarantees

- **Nothing touches the live store by default.** `MemoraLongTermStore` is never
  instantiated by the engine, demo, CLI, or tests; defaults are
  `InMemory`/`SqliteLongTermStore` under a scratch path.
- **`MemoraLongTermStore` is guarded:** requires an explicit `db_path`, refuses to
  run if `MEMORA_STORAGE_URI` is set, and after connecting asserts via
  `PRAGMA database_list` that it landed on the requested file — else it aborts.
- **The `claude -p` adaptor is sandboxed** (`--strict-mcp-config` + empty MCP
  config + `--max-turns 1`): no MCP servers load, so it cannot reach memora; it
  returns text only. Args are passed as an argv list (no shell).
- **Promotion is gated** (`MEM_ENGINE_AUTOWRITE`); when off it logs what's ready
  and writes nothing, leaving episodes to retry.

## Known limitations / next steps

- The `HashingEmbedder` fallback is **lexical, not semantic** (`write` ≠ `writing`);
  install `sentence-transformers` (bge-small) for real recall quality. The engine
  lowers `min_score` automatically when on the fallback.
- The `ClaudeAdaptor` prompt hard-codes example hub slugs. For real use, feed it
  the **live hub list** (`memory_list(tags_any=["index"])`) so leaves link to hubs
  that actually exist (the demo produced `[[working-style-hub]]`, which doesn't yet).
- Short-term dedup compares against the most recent 400 unpromoted episodes.
- Not yet wired into `server.py`; this is a prototype on branch `feat/engine-v2`.
