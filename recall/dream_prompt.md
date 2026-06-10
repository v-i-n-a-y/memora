# Memora nightly consolidation ("dream")

You are running unattended as a nightly memory-consolidation job for Vinay's memora store. Be conservative: when unsure whether two memories are truly redundant or superseded, leave them alone. Memora keeps lineage, but wrong merges still degrade recall.

First, read the `memora-usage-conventions` memory (`memory_list` with query "memora-usage-conventions") and follow its naming, linking, and tagging rules for everything below.

Then do the following, in order:

## 1. Learn from recent prompts
Read `/Users/vinay/Documents/Projects/memora/recall/dream_inbox.jsonl` (one JSON object per line: `ts`, `query` = a prompt Vinay typed, `recalled` = [memory_id, score] pairs that were auto-recalled for it).

- Identify recurring themes across the prompts: topics Vinay keeps asking about.
- For each recurring theme, check whether memora already covers it well (semantic search). If coverage is weak or missing AND the theme reflects durable context (a project, preference, or ongoing concern — not ephemeral task chatter), create a concise new memory for it, properly tagged per the conventions (type tag + focus:/project: tags where they apply).
- Queries that repeatedly recalled nothing or only low scores (<0.35) are the strongest signal of a coverage gap.
- Do NOT store the prompts themselves or transient task details.

## 2. Consolidate the store
- `memory_find_duplicates` — merge genuine duplicates with `memory_merge` (keep the richer entry).
- `memory_detect_supersessions` — where one memory clearly supersedes another, record the supersession. If it reports `llm_unavailable`, do the classification yourself: take its top candidate pairs (similarity >= 0.8), read both memories with `memory_get`, and where one is clearly a newer version of the same fact, link them with `memory_link` (supersedes edge) — judge at most the top 10 pairs per night.
- `memory_rebuild_crossrefs` — rebuild automatic links between related memories.
- `memory_validate_tags` / `memory_backfill_tags` — fix tagging drift.
- Convention backfill: any memory missing `metadata.name` (the kebab-case slug required by the conventions memory) gets one added via `memory_update` — derive it from the memory's subject (e.g. `perseus-branch-layout`). Check the handful of most recently created/updated memories rather than scanning the whole store every night.
- Use `memory_insights` / `memory_stats` to spot anything degenerate (orphaned, contradictory, or stale memories). Stale memories that were superseded by observable reality should be updated or deleted; if a memory is merely old but still plausibly true, leave it (natural decay already lowers its rank).

## 3. Report
Finish with a short plain-text summary: themes distilled (with new memory IDs), merges/supersessions made, links rebuilt, anything skipped out of caution. This goes to a log file, not to Vinay directly, so keep it factual and brief.
