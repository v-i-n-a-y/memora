"""Background worker for off-path embedding + crossref computation.

memory_create and memory_update would otherwise block their MCP turn on
~1-2 s of OpenAI embedding time plus the crossref scan. We instead INSERT
and FTS-index inline, then enqueue the slow half for a daemon thread to
finish asynchronously. The MCP tool returns in <500 ms; the embedding and
crossrefs land a moment later and are broadcast to the graph UI.

Design constraints:
  * Single worker thread. ``D1Connection`` (backends.py) keeps a per-instance
    session token — parallel fan-out across threads would fight for it.
  * The worker owns its own long-lived DB connection. Phase-0 schema caching
    (schema.py) lives on the backend, so reopening per job is technically
    safe, but the HTTP handshake overhead is wasted work. Instead we reseed
    the connection's D1 session bookmark from the backend singleton before
    each job — that's what gives us read-your-writes against the caller's
    just-committed INSERT/UPDATE. See D1Connection / D1Backend in backends.py.
  * Job failures are logged, not raised. An unembedded memory is annoying
    but recoverable via ``memory_rebuild_embeddings``; a crashed worker
    thread would silently halt all future jobs.

Public API:
  * ``enqueue_embedding_job(memory_id, *, update_crossrefs) -> bool``
    Returns True if the job was queued for the worker, False if no worker
    is running. Callers that get False and need the embedding now should
    fall back to ``run_embedding_job_inline``.
  * ``run_embedding_job_inline(conn, memory_id, *, update_crossrefs) -> list[dict]``
    Runs the embedding + crossref work synchronously on the caller's
    connection. Returns the related list. Used by tests and one-shot
    scripts that haven't started a worker.
  * ``start_worker()`` / ``flush(timeout)`` — lifecycle helpers.
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _EmbeddingJob:
    memory_id: int
    update_crossrefs: bool


_QUEUE: "queue.Queue[Optional[_EmbeddingJob]]" = queue.Queue()
_WORKER: Optional[threading.Thread] = None
_WORKER_LOCK = threading.Lock()


def enqueue_embedding_job(memory_id: int, *, update_crossrefs: bool) -> bool:
    """Queue an embedding job for the background worker.

    Returns True if the job was queued, False if no worker is running.
    Callers that need the embedding synchronously should check the return
    value and invoke ``run_embedding_job_inline`` on False.

    Safe to call from any thread.
    """
    if _WORKER is None or not _WORKER.is_alive():
        return False
    _QUEUE.put(_EmbeddingJob(memory_id=memory_id, update_crossrefs=update_crossrefs))
    return True


def run_embedding_job_inline(
    conn,
    memory_id: int,
    *,
    update_crossrefs: bool,
) -> "list[dict]":
    """Run the embedding + crossref work synchronously on the caller's conn.

    Uses ``conn`` for all reads/writes to avoid SQLite write-lock contention
    against the caller's open transaction. Commits on the caller's behalf
    so the embedding upsert persists when the outer context closes.

    Returns the freshly computed related list (empty if crossrefs are
    skipped for this memory, e.g. section or document-fragment types).
    """
    try:
        return _process_job(conn, _EmbeddingJob(memory_id, update_crossrefs), broadcast=False)
    except Exception:
        logger.exception("Inline embedding job failed for memory_id=%s", memory_id)
        return []


def flush(timeout: Optional[float] = None) -> None:
    """Block until all currently-queued jobs are processed.

    Intended for tests and graceful shutdowns. No-op if the worker isn't
    running.
    """
    if _WORKER is None or not _WORKER.is_alive():
        return
    if timeout is None:
        _QUEUE.join()
    else:
        # queue.Queue has no join-with-timeout, so poll.
        import time
        deadline = time.monotonic() + timeout
        while _QUEUE.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.01)


def start_worker() -> None:
    """Start the background worker thread. Idempotent."""
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.is_alive():
            return
        t = threading.Thread(target=_run, name="memora-embedding-worker", daemon=True)
        t.start()
        _WORKER = t


def _run() -> None:
    """Worker loop. Holds a single long-lived DB connection."""
    from .storage import connect

    try:
        conn = connect(check_same_thread=False)
    except Exception:
        logger.exception("Embedding worker could not open DB connection; aborting")
        return

    while True:
        job = _QUEUE.get()
        if job is None:
            _QUEUE.task_done()
            return
        try:
            _refresh_bookmark(conn)
            _process_job(conn, job, broadcast=True)
        except Exception:
            logger.exception("Embedding job failed for memory_id=%s", job.memory_id)
        finally:
            _QUEUE.task_done()


def _refresh_bookmark(conn) -> None:
    """Seed the D1 connection with the backend's latest session bookmark.

    The worker holds a long-lived connection; without this, its session
    token is frozen at worker-startup time and a SELECT can miss the
    caller's just-committed INSERT/UPDATE (read-your-writes violation).
    No-op for non-D1 connections (local SQLite sees cross-connection
    commits natively via the shared database file).
    """
    backend = getattr(conn, "_backend", None)
    if backend is None:
        return
    getter = getattr(backend, "get_latest_bookmark", None)
    if getter is None:
        return
    latest = getter()
    if latest is None:
        return
    existing = getattr(conn, "_session_token", None)
    # Bookmarks sort lexicographically oldest-to-newest; only advance.
    if existing is None or latest > existing:
        conn._session_token = latest


def _process_job(conn, job: _EmbeddingJob, *, broadcast: bool) -> "list[dict]":
    """Compute embedding, upsert, refresh crossrefs, commit, optionally broadcast.

    Re-fetches content from the DB rather than taking it on the job so that
    a pending job sees the *latest* version if the memory has been updated
    while queued.

    Always commits so inline-fallback work persists when the caller closes
    its connection. The caller's INSERT/UPDATE has already been committed
    before we get here, so we're only flushing the embedding upsert and
    optional crossref store.

    ``broadcast=False`` suppresses ``schedule_sync()`` for the inline path
    where the caller's outer flow already drives graph updates.

    Returns the freshly computed related list when crossrefs were updated.
    """
    from .storage import (
        _compute_embedding,
        _should_skip_crossrefs,
        _update_crossrefs_for_memory,
        _upsert_embedding,
        get_memory,
    )

    record = get_memory(conn, job.memory_id)
    if record is None:
        logger.debug("Embedding job skipped: memory %s no longer exists", job.memory_id)
        return []

    vector = _compute_embedding(
        record["content"],
        record.get("metadata"),
        record.get("tags", []),
    )
    _upsert_embedding(conn, job.memory_id, vector)

    related: "list[dict]" = []
    if job.update_crossrefs and not _should_skip_crossrefs(record.get("metadata")):
        related = _update_crossrefs_for_memory(conn, job.memory_id, vector=vector)

    conn.commit()
    if broadcast:
        from .cloud_sync import schedule_sync
        schedule_sync()
    return related
