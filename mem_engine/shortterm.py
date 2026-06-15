"""Working memory (Tier 1): local, no-LLM short-term store.

Ingests observations (conversation turns or arbitrary facts), dedups
near-identical ones into a recurrence count, flags durable cues, and expires
chatter by TTL. Nothing here costs an LLM token; only promotion (promote.py)
does, and only for episodes that have proven they persist. Heuristics/thresholds
are lifted from the proven archive/recall-subsystem consolidator.
"""
from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from .embedder import cosine, get_embedder

DEFAULT_DURABLE_CUES = (
    "always", "never", "from now on", "i prefer", "prefer ", "don't", "do not",
    "make sure", "going forward", "instead of", "stop ", "no longer", "remember",
    "in future", "each time", "whenever", "by default",
)

_SCHEMA = """
create table if not exists episodes(
    id integer primary key,
    first_ts real, last_ts real, seen integer,
    session text, cwd text, kind text,
    text text, emb text, meta text,
    durable integer default 0,
    promoted integer default 0,
    status text default 'pending',
    memory_name text
)
"""


@dataclass
class Episode:
    id: int
    text: str
    seen: int
    durable: int
    first_ts: float
    last_ts: float
    age_hours: float
    session: Optional[str] = None
    cwd: Optional[str] = None
    kind: str = "turn"

    def to_dict(self) -> Dict:
        return dict(self.__dict__)


class WorkingMemory:
    def __init__(
        self,
        db_path: str = ":memory:",
        embedder=None,
        *,
        clock: Callable[[], float] = time.time,
        dedup_cosine: float = 0.85,
        recent_window: int = 400,
        persist_hours: float = 12.0,
        expire_days: float = 14.0,
        max_unpromoted_days: float = 60.0,
        promote_batch: int = 12,
        min_len: int = 12,
        durable_cues: Tuple[str, ...] = DEFAULT_DURABLE_CUES,
    ):
        self.embedder = embedder or get_embedder("auto")
        self.clock = clock
        self.dedup_cosine = dedup_cosine
        self.recent_window = recent_window
        self.persist_hours = persist_hours
        self.expire_days = expire_days
        self.max_unpromoted_days = max_unpromoted_days
        self.promote_batch = promote_batch
        self.min_len = min_len
        self.durable_cues = tuple(durable_cues)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- ingest --------------------------------------------------------------
    def observe(self, text: str, session=None, cwd=None, kind="turn", meta=None) -> Dict:
        text = (text or "").strip()
        if len(text) < self.min_len:
            return {"id": None, "duplicate": False, "skipped": "too_short"}
        emb = self.embedder.embed(text)
        now = self.clock()
        recent = self._conn.execute(
            "select id, emb from episodes where promoted=0 order by last_ts desc limit ?",
            (self.recent_window,),
        ).fetchall()
        dup_id = None
        for eid, emb_json in recent:
            try:
                if cosine(emb, json.loads(emb_json)) >= self.dedup_cosine:
                    dup_id = eid
                    break
            except Exception:
                continue
        if dup_id is not None:
            self._conn.execute(
                "update episodes set seen=seen+1, last_ts=? where id=?", (now, dup_id)
            )
            self._conn.commit()
            return {"id": dup_id, "duplicate": True}
        durable = 1 if any(c in text.lower() for c in self.durable_cues) else 0
        cur = self._conn.execute(
            "insert into episodes(first_ts,last_ts,seen,session,cwd,kind,text,emb,meta,durable)"
            " values(?,?,?,?,?,?,?,?,?,?)",
            (now, now, 1, session, cwd, kind, text,
             json.dumps([float(x) for x in emb]), json.dumps(meta or {}), durable),
        )
        self._conn.commit()
        return {"id": cur.lastrowid, "duplicate": False, "durable": bool(durable)}

    # -- promotion gate ------------------------------------------------------
    def ready_for_promotion(self) -> List[Episode]:
        now = self.clock()
        out: List[Episode] = []
        rows = self._conn.execute(
            "select id,text,seen,durable,first_ts,last_ts,session,cwd,kind"
            " from episodes where promoted=0 order by last_ts desc"
        ).fetchall()
        for (eid, text, seen, durable, first_ts, last_ts, session, cwd, kind) in rows:
            age_h = (now - first_ts) / 3600.0
            if seen >= 2 or (durable and age_h >= self.persist_hours):
                out.append(Episode(eid, text, seen, durable, first_ts, last_ts,
                                    round(age_h, 2), session, cwd, kind or "turn"))
            if len(out) >= self.promote_batch:
                break
        return out

    def mark_promoted(self, outcomes: Dict[int, Tuple[str, Optional[str]]]) -> None:
        for eid, (status, name) in outcomes.items():
            self._conn.execute(
                "update episodes set promoted=1, status=?, memory_name=? where id=?",
                (status, name, eid),
            )
        self._conn.commit()

    def expire(self) -> int:
        now = self.clock()
        cutoff = now - self.expire_days * 86400
        removed = self._conn.execute(
            "delete from episodes where last_ts < ? and (promoted=1 or (durable=0 and seen<2))",
            (cutoff,),
        ).rowcount
        # Hard cap: a long-gated queue (durable-pending rows that never promote)
        # must not grow unbounded — drop anything older than max_unpromoted_days.
        hard = now - self.max_unpromoted_days * 86400
        removed += self._conn.execute(
            "delete from episodes where last_ts < ?", (hard,)
        ).rowcount
        self._conn.commit()
        return removed

    # -- inspection ----------------------------------------------------------
    def pending_count(self) -> int:
        return self._conn.execute(
            "select count(*) from episodes where promoted=0"
        ).fetchone()[0]

    def stats(self) -> Dict:
        c = self._conn
        total = c.execute("select count(*) from episodes").fetchone()[0]
        promoted = c.execute("select count(*) from episodes where promoted=1").fetchone()[0]
        durable = c.execute(
            "select count(*) from episodes where durable=1 and promoted=0"
        ).fetchone()[0]
        return {"total": total, "pending": total - promoted,
                "promoted": promoted, "durable_pending": durable}
