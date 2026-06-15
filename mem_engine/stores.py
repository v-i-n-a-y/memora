"""Long-term store abstraction (the hub-and-spoke graph).

The engine writes atomic leaves and queries for dedup/recall through this small
interface. Three implementations:

  - InMemoryLongTermStore : hermetic, for tests.
  - SqliteLongTermStore   : self-contained scratch store (memora-shaped rows),
                            used by the demo WITHOUT importing memora.
  - MemoraLongTermStore   : adapter over memora.storage for real wiring
                            (lazy import; point it at a scratch db, never prod).

A hit is a dict: {id, name, content, tags, score}. `preview` is the leaf's
first sentence (recall returns thin pointers, not full bodies).
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Dict, List, Optional, Protocol

from .embedder import cosine, get_embedder
from .schema import Leaf


def first_sentence(content: str) -> str:
    s = (content or "").strip()
    period = s.find(". ")
    newline = s.find("\n")
    cands = [i for i in (period, newline) if i != -1]
    if not cands:
        return s
    cut = min(cands)
    return (s[:cut + 1] if cut == period else s[:cut]).strip()


class LongTermStore(Protocol):
    def add(self, leaf: Leaf) -> int: ...
    def search(self, query: str, *, top_k: int = 5, min_score: float = 0.0) -> List[Dict]: ...
    def count(self) -> int: ...


class InMemoryLongTermStore:
    """Hermetic store for tests; cosine search over the engine embedder."""

    def __init__(self, embedder=None):
        self.embedder = embedder or get_embedder("auto")
        self._rows: List[Dict] = []
        self._next = 1

    def add(self, leaf: Leaf) -> int:
        payload = leaf.to_memora()
        mid = self._next
        self._next += 1
        self._rows.append({
            "id": mid,
            "name": leaf.name,
            "content": payload["content"],
            "tags": payload["tags"],
            "emb": self.embedder.embed(payload["content"]),
        })
        return mid

    def search(self, query: str, *, top_k: int = 5, min_score: float = 0.0) -> List[Dict]:
        q = self.embedder.embed(query)
        scored = []
        for r in self._rows:
            s = cosine(q, r["emb"])
            if s >= min_score:
                scored.append((s, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{
            "id": r["id"], "name": r["name"], "content": r["content"],
            "tags": r["tags"], "score": round(float(s), 4),
            "preview": first_sentence(r["content"]),
        } for s, r in scored[:top_k]]

    def count(self) -> int:
        return len(self._rows)

    def all(self) -> List[Dict]:
        return [{"id": r["id"], "name": r["name"], "content": r["content"], "tags": r["tags"]}
                for r in self._rows]


class SqliteLongTermStore:
    """Scratch sqlite store shaped like a memora memory; no memora dependency."""

    _SCHEMA = """
    create table if not exists memories(
        id integer primary key, name text, content text,
        tags text, metadata text, emb text, created_at real
    )"""

    def __init__(self, db_path: str, embedder=None):
        self.embedder = embedder or get_embedder("auto")
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(self._SCHEMA)
        self._conn.commit()

    def add(self, leaf: Leaf) -> int:
        p = leaf.to_memora()
        emb = self.embedder.embed(p["content"])
        cur = self._conn.execute(
            "insert into memories(name,content,tags,metadata,emb,created_at) values(?,?,?,?,?,?)",
            (leaf.name, p["content"], json.dumps(p["tags"]),
             json.dumps(p["metadata"]), json.dumps([float(x) for x in emb]), time.time()),
        )
        self._conn.commit()
        return cur.lastrowid

    def search(self, query: str, *, top_k: int = 5, min_score: float = 0.0) -> List[Dict]:
        q = self.embedder.embed(query)
        scored = []
        for mid, name, content, tags, emb in self._conn.execute(
            "select id,name,content,tags,emb from memories"
        ).fetchall():
            try:
                s = cosine(q, json.loads(emb))
            except Exception:
                continue
            if s >= min_score:
                scored.append((s, mid, name, content, tags))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{
            "id": mid, "name": name, "content": content,
            "tags": json.loads(tags or "[]"), "score": round(float(s), 4),
            "preview": first_sentence(content),
        } for s, mid, name, content, tags in scored[:top_k]]

    def count(self) -> int:
        return self._conn.execute("select count(*) from memories").fetchone()[0]


class MemoraLongTermStore:
    """Adapter over memora.storage — the real-wiring path.

    Safety: this is the ONLY class that can reach a real memora DB, so it
    REQUIRES an explicit scratch db_path, refuses to run if MEMORA_STORAGE_URI
    is set (which would override the path), and after connecting it asserts via
    PRAGMA that it actually landed on the requested file — aborting otherwise.
    That guarantees it cannot silently write to the production store or remote
    server regardless of how memora resolves its config.
    """

    def __init__(self, db_path: str):
        import os
        if not db_path:
            raise ValueError(
                "MemoraLongTermStore requires an explicit scratch db_path; "
                "refusing to default to the production memora database")
        if os.environ.get("MEMORA_STORAGE_URI"):
            raise RuntimeError(
                "MEMORA_STORAGE_URI is set and would override db_path; refusing to run "
                "so a scratch path cannot be silently redirected to prod/remote")
        db_path = os.path.abspath(db_path)
        os.environ["MEMORA_DB_PATH"] = db_path
        from memora import storage  # lazy; pulls memora's deps only on real wiring
        self._storage = storage
        self._conn = storage.connect()
        storage.ensure_schema(self._conn)
        rows = self._conn.execute("PRAGMA database_list").fetchall()
        main_file = next((r[2] for r in rows if r[1] == "main"), "") or ""
        if os.path.abspath(main_file) != db_path:
            self._conn.close()
            raise RuntimeError(
                f"memora resolved its DB to {main_file!r}, not the requested scratch "
                f"path {db_path!r}; aborting to protect the live store")

    def add(self, leaf: Leaf) -> int:
        rec = self._storage.add_memory(self._conn, **leaf.to_memora())
        return int(rec.get("id")) if isinstance(rec, dict) else int(rec)

    def search(self, query: str, *, top_k: int = 5, min_score: float = 0.0) -> List[Dict]:
        hits = self._storage.semantic_search(self._conn, query, top_k=top_k, min_score=min_score)
        out = []
        for h in hits:
            mem = h.get("memory", h) if isinstance(h, dict) else {}
            md = mem.get("metadata") or {}
            content = mem.get("content") or mem.get("content_preview") or ""
            out.append({
                "id": mem.get("id"),
                "name": md.get("name"),
                "content": content,
                "tags": mem.get("tags") or [],
                "score": round(float(h.get("score", 0.0)), 4) if isinstance(h, dict) else 0.0,
                "preview": first_sentence(content),
            })
        return out

    def count(self) -> int:
        try:
            return int(self._storage.get_statistics(self._conn).get("total_memories", 0))
        except Exception:
            return -1
