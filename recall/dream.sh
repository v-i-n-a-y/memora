#!/bin/bash
# Nightly memora consolidation: rotate the recall log into an inbox,
# run a headless Claude agent over it, archive the inbox on success.
set -u
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="$DIR/recall_log.jsonl"
INBOX="$DIR/dream_inbox.jsonl"
ARCHIVE="$DIR/recall_log.processed.jsonl"
RUNLOG="$DIR/dream_runs.log"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
export CLAUDE_DREAM_RUN=1  # suppresses the memora save-nudge Stop hook

# Embeddings are local (sentence-transformers/bge, in-process) and memora's
# internal LLM is disabled; reasoning is done by the `claude -p` agent below.
# Ollama is no longer used by the memory system.

# Rotate: append current log to inbox (inbox survives a failed run)
if [ -s "$LOG" ]; then
  cat "$LOG" >> "$INBOX" && : > "$LOG"
fi

if [ ! -s "$INBOX" ]; then
  echo "$(date '+%Y-%m-%d %H:%M:%S') skipped: no recall activity" >> "$RUNLOG"
  exit 0
fi

echo "$(date '+%Y-%m-%d %H:%M:%S') dream run starting ($(wc -l < "$INBOX" | tr -d ' ') log lines)" >> "$RUNLOG"

claude -p "$(cat "$DIR/dream_prompt.md")" \
  --allowedTools "mcp__memora-server,mcp__memora-server__*,Read,ToolSearch" \
  --max-turns 60 \
  >> "$RUNLOG" 2>&1
STATUS=$?

if [ $STATUS -eq 0 ]; then
  cat "$INBOX" >> "$ARCHIVE" && rm -f "$INBOX"
  echo "$(date '+%Y-%m-%d %H:%M:%S') dream run completed" >> "$RUNLOG"
else
  echo "$(date '+%Y-%m-%d %H:%M:%S') dream run FAILED (exit $STATUS), inbox retained" >> "$RUNLOG"
fi
exit $STATUS
