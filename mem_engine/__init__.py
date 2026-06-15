"""mem_engine — server-side working-memory + atomic-leaf promotion engine for memora (v2).

A hub-and-spoke memory engine that can be driven by any MCP client (not bound to
Claude Code hooks):

  observe(turn/fact)  ->  working memory (local, no LLM, dedup + recurrence + TTL)
        |  recurrence>=2 OR durable cue + dwell
        v
  promote()  ->  LLM ingest adaptor distils episodes into atomic LEAVES
        |       (adaptor returns structured JSON; the engine validates the
        |        house-style schema and persists — the LLM never writes directly)
        v
  long-term store (the hub-and-spoke graph)

  recall_for_prompt()  ->  thin pointers (id + 1-line + score) into the graph;
                           the caller fetches full leaves on demand.

Core modules depend only on stdlib + numpy so the whole engine runs and tests
hermetically with no model download and no network. bge embeddings and the
real `claude -p` adaptor are optional, lazily loaded enhancements.
"""

__version__ = "0.1.0"
