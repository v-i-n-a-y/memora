You are distilling Vinay's persistent short-term observations into long-term memora memories. Run unattended and be concise.

STEP 1. Read the store conventions: `memory_list(query="memora-usage-conventions")` then memory_get that memory. Obey it, especially: the record type lives in `metadata.type` (user/feedback/project/reference); NEVER attach generic tags like `reference`/`note`/`project` (they corrupt the tag vocabulary); set `metadata.name` (kebab-case) and `section` + `hierarchy.path`; use `focus:evandor`/`focus:astrodynamic`/`focus:phd` and `project:<name>` tags where relevant; people go in section `contacts` per the contacts convention.

STEP 2. Read the JSON file at {{PAYLOAD}} — a list of observations that have already persisted (recurred, or dwelt past a threshold) and are therefore candidates for long-term memory. Each has `user` and `assistant` text plus `seen`/`age_hours`.

STEP 3. DISTILL. Group related observations and extract only the genuinely durable signal: stable user preferences, corrections/feedback on how to work, project facts or decisions, or external references. Ignore ephemeral task state, one-off details, and anything already obvious from code or CLAUDE.md. Merge several related observations into ONE memory rather than many.

STEP 4. For each distilled fact:
  a. `memory_semantic_search` the store for an existing memory on the same point.
  b. If a clear match exists, do NOT duplicate — skip it (leave the existing memory as is).
  c. Otherwise `memory_create` it: distilled and self-contained, first sentence a summary; for feedback/project memories include a **Why:** line and a **How to apply:** line. UK English, no em dashes. Tag and type per STEP 1.

STEP 5. Output, as the FINAL line, exactly `RESULTS: ` followed by a JSON array with one object per input episode id from {{PAYLOAD}}:
  {"id": <episode id>, "outcome": "stored" | "duplicate" | "ephemeral", "memory_name": "<slug or null>"}
where "stored" = the episode contributed to a memory you created (give its name), "duplicate" = its point already exists in long-term, "ephemeral" = not durable, nothing stored. You may print a one-sentence summary before that line, but the RESULTS line must come last.

If nothing in the batch is genuinely durable, create nothing and mark every id "ephemeral".
