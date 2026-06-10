#!/usr/bin/env python3
"""
migrate_embeddings.py — Re-embed the live memora DB using the recommended
sentence-transformers model (BAAI/bge-small-en-v1.5).

Usage:
    MEMORA_MIGRATE_CONFIRM=1 \
    MEMORA_EMBEDDING_MODEL=sentence-transformers \
    SENTENCE_TRANSFORMERS_MODEL=BAAI/bge-small-en-v1.5 \
    /Users/vinay/.local/share/uv/tools/memora-mcp/bin/python migrate_embeddings.py

Guards:
  - Refuses to run unless MEMORA_MIGRATE_CONFIRM=1 is set.
  - Backs up the live DB to ~/.local/share/memora/backups/ before any writes.

After running, set in memora config:
    MIN_SCORE = 0.62
    EMBEDDING_MODEL = sentence-transformers
    SENTENCE_TRANSFORMERS_MODEL = BAAI/bge-small-en-v1.5
"""

import os
import sys
import sqlite3
import shutil
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Guard: explicit opt-in required
# ---------------------------------------------------------------------------
if os.environ.get("MEMORA_MIGRATE_CONFIRM") != "1":
    print(
        "ERROR: Safety guard triggered.\n"
        "This script modifies the LIVE memora database.\n"
        "Re-run with MEMORA_MIGRATE_CONFIRM=1 to proceed.",
        file=sys.stderr,
    )
    sys.exit(1)

# ---------------------------------------------------------------------------
# Env checks
# ---------------------------------------------------------------------------
embedding_model = os.environ.get("MEMORA_EMBEDDING_MODEL", "sentence-transformers")
st_model = os.environ.get("SENTENCE_TRANSFORMERS_MODEL", "BAAI/bge-small-en-v1.5")

if embedding_model != "sentence-transformers":
    print(
        f"ERROR: MEMORA_EMBEDDING_MODEL must be 'sentence-transformers', got {embedding_model!r}",
        file=sys.stderr,
    )
    sys.exit(1)

print(f"Embedding backend : {embedding_model}")
print(f"ST model          : {st_model}")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
db_path = Path.home() / ".local" / "share" / "memora" / "memories.db"
backup_dir = Path.home() / ".local" / "share" / "memora" / "backups"

if not db_path.exists():
    print(f"ERROR: Live DB not found at {db_path}", file=sys.stderr)
    sys.exit(1)

# ---------------------------------------------------------------------------
# Backup via sqlite3 .backup API (safe online backup)
# ---------------------------------------------------------------------------
backup_dir.mkdir(parents=True, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
backup_path = backup_dir / f"memories_pre_migration_{ts}.db"

print(f"Backing up {db_path} -> {backup_path} ...")
src_conn = sqlite3.connect(str(db_path))
dst_conn = sqlite3.connect(str(backup_path))
src_conn.backup(dst_conn)
dst_conn.close()
src_conn.close()
print(f"Backup complete ({backup_path.stat().st_size:,} bytes).")

# ---------------------------------------------------------------------------
# Set env so memora's compute_embedding picks up the right ST model
# ---------------------------------------------------------------------------
os.environ["SENTENCE_TRANSFORMERS_MODEL"] = st_model
os.environ["MEMORA_EMBEDDING_MODEL"] = embedding_model

# ---------------------------------------------------------------------------
# Re-embed
# ---------------------------------------------------------------------------
from memora.embeddings import rebuild_all_embeddings  # noqa: E402

print("Opening live DB and rebuilding embeddings ...")
live_conn = sqlite3.connect(str(db_path))
live_conn.row_factory = sqlite3.Row

count = rebuild_all_embeddings(live_conn, embedding_model)
live_conn.close()

print(f"Done. {count} memories re-embedded with {st_model}.")
print()
print("Next steps — update your memora config/env:")
print(f"  MEMORA_EMBEDDING_MODEL=sentence-transformers")
print(f"  SENTENCE_TRANSFORMERS_MODEL={st_model}")
print(f"  MEMORA_MIN_SCORE=0.62  (or equivalent config key)")
