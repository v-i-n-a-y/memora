"""Hermetic test suite for mem_engine.

Runs on stdlib + numpy only (HashingEmbedder + MockAdaptor) — no network, no
model download, no memora, no live store. Exercises every component plus the
full observe -> consolidate -> recall loop through the MCP-tool surface.

    python3 -m unittest mem_engine.tests.test_engine -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np  # noqa: E402

from mem_engine import mcp_tools, schema  # noqa: E402
from mem_engine.adaptor import (DistillResult, MockAdaptor,  # noqa: E402
                                _extract_json, _result_from_json)
from mem_engine.embedder import HashingEmbedder, cosine  # noqa: E402
from mem_engine.engine import Engine, EngineConfig  # noqa: E402
from mem_engine.schema import Leaf  # noqa: E402
from mem_engine.shortterm import Episode, WorkingMemory  # noqa: E402
from mem_engine.stores import InMemoryLongTermStore  # noqa: E402


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


class EmptyAdaptor:
    """Adversarial adaptor: returns no leaves and no outcomes at all."""

    def distill(self, episodes):
        return DistillResult(leaves=[], outcomes={})


class TestEmbedder(unittest.TestCase):
    def test_deterministic_and_normalized(self):
        e = HashingEmbedder()
        v1, v2 = e.embed("perseus jax training"), e.embed("perseus jax training")
        self.assertTrue(np.array_equal(v1, v2))
        self.assertAlmostEqual(float(np.linalg.norm(v1)), 1.0, places=5)

    def test_similarity_orders(self):
        e = HashingEmbedder()
        q = e.embed("perseus training uses jax")
        near = e.embed("perseus training is built on jax")
        far = e.embed("evandor psu board buck converter")
        self.assertGreater(cosine(q, near), cosine(q, far))


class TestSchema(unittest.TestCase):
    def test_valid_leaf(self):
        leaf = Leaf(content="Perseus is JAX-only since 2026-05-18.",
                    name="perseus-jax-only", type="project", section="phd",
                    tags=["focus:phd", "project:perseus"], links=["perseus-hub"])
        self.assertTrue(schema.is_valid(leaf), schema.validate(leaf))

    def test_invalid_slug_type_section_tags(self):
        leaf = Leaf(content="x" * 20, name="Perseus JAX", type="bug",
                    section="nope", tags=["reference", "focus:phd"], links=[])
        errs = " ".join(schema.errors(leaf))
        self.assertIn("kebab", errs)
        self.assertIn("generic tags", errs)
        self.assertIn("type", errs)
        self.assertIn("section", errs)

    def test_hard_size_cap(self):
        leaf = Leaf(content="a" * 2500, name="big-leaf", type="reference",
                    section="phd", links=["phd-working-tree"])
        self.assertIn("hard cap", " ".join(schema.errors(leaf)))

    def test_to_memora_links_footer_and_metadata(self):
        leaf = Leaf(content="Body.", name="x-y", type="project", section="phd",
                    links=["phd-working-tree"])
        m = leaf.to_memora()
        self.assertIn("[[phd-working-tree]]", m["content"])
        self.assertEqual(m["metadata"]["name"], "x-y")
        self.assertEqual(m["metadata"]["hierarchy"]["path"], ["phd"])

    def test_date_suffix_validation(self):
        good = Leaf(content="A run happened.", name="perseus-run-2026-05-13",
                    type="project", section="phd", links=["perseus-hub"])
        self.assertTrue(schema.is_valid(good), schema.validate(good))
        bad = Leaf(content="A run happened.", name="perseus-run-2026-13-99",
                   type="project", section="phd", links=["perseus-hub"])
        self.assertIn("invalid date", " ".join(schema.errors(bad)))
        impossible = Leaf(content="A run happened.", name="perseus-run-2026-02-31",
                          type="project", section="phd", links=["perseus-hub"])
        self.assertIn("invalid date", " ".join(schema.errors(impossible)))


class TestWorkingMemory(unittest.TestCase):
    def _wm(self, clk):
        return WorkingMemory(":memory:", embedder=HashingEmbedder(), clock=clk)

    def test_recurrence_dedup(self):
        clk = Clock()
        wm = self._wm(clk)
        r1 = wm.observe("Perseus DDP training hangs on the workstation with two ranks")
        r2 = wm.observe("Perseus DDP training hangs on the workstation with two ranks")
        self.assertFalse(r1["duplicate"])
        self.assertTrue(r2["duplicate"])
        ready = wm.ready_for_promotion()
        self.assertEqual(len(ready), 1)
        self.assertGreaterEqual(ready[0].seen, 2)

    def test_durable_gate_respects_clock(self):
        clk = Clock()
        wm = self._wm(clk)
        res = wm.observe("From now on always write in UK English, no em dashes")
        self.assertTrue(res.get("durable"))
        self.assertEqual(len(wm.ready_for_promotion()), 0)  # too fresh
        clk.advance(13 * 3600)
        self.assertEqual(len(wm.ready_for_promotion()), 1)  # dwelt past 12h

    def test_expire_removes_ephemeral_only(self):
        clk = Clock()
        wm = self._wm(clk)
        wm.observe("A one-off ephemeral note about nothing in particular right now")
        clk.advance(15 * 86400)
        self.assertEqual(wm.expire(), 1)
        self.assertEqual(wm.pending_count(), 0)

    def test_expire_hard_cap_evicts_durable_pending(self):
        clk = Clock()
        wm = WorkingMemory(":memory:", embedder=HashingEmbedder(), clock=clk,
                           max_unpromoted_days=60)
        wm.observe("From now on always handle the durable thing in a specific way")
        clk.advance(61 * 86400)
        self.assertEqual(wm.expire(), 1)  # hard cap evicts the durable-pending leak
        self.assertEqual(wm.pending_count(), 0)


class TestMockAdaptor(unittest.TestCase):
    def test_produces_valid_leaves(self):
        eps = [Episode(1, "Perseus training uses JAX and flax nnx on the workstation",
                       2, 0, 0, 0, 0.0),
               Episode(2, "From now on always use UK English in my writing",
                       1, 1, 0, 0, 13.0)]
        res = MockAdaptor().distill(eps)
        self.assertEqual(len(res.leaves), 2)
        for leaf in res.leaves:
            self.assertTrue(schema.is_valid(leaf), schema.validate(leaf))
        self.assertEqual(res.outcomes[1][0], "stored")

    def test_claude_json_parsing(self):
        sample = (
            "preamble\n```json\n"
            '{"leaves":[{"name":"perseus-jax-only","type":"project","section":"phd",'
            '"tags":["project:perseus"],"links":["perseus-hub"],'
            '"content":"Perseus is JAX-only.","source_episode_ids":[7]}],'
            '"outcomes":[{"id":7,"outcome":"stored","memory_name":"perseus-jax-only"}]}'
            "\n```\n"
        )
        obj = _extract_json(sample)
        self.assertIsNotNone(obj)
        res = _result_from_json(obj, [Episode(7, "perseus jax only", 2, 0, 0, 0, 0.0)])
        self.assertEqual(len(res.leaves), 1)
        self.assertEqual(res.outcomes[7][0], "stored")
        self.assertTrue(schema.is_valid(res.leaves[0]))


class TestPromoteAndRecall(unittest.TestCase):
    def _engine(self, enabled):
        emb = HashingEmbedder()
        wm = WorkingMemory(":memory:", embedder=emb, clock=Clock())
        lt = InMemoryLongTermStore(embedder=emb)
        return Engine(wm, lt, MockAdaptor(), EngineConfig(enabled=enabled, min_score=0.0))

    def test_gate_blocks_then_writes(self):
        eng = self._engine(False)
        eng.observe("Perseus DDP training hangs on workstation two ranks cusparse spmm")
        eng.observe("Perseus DDP training hangs on workstation two ranks cusparse spmm")
        s1 = eng.consolidate()
        self.assertTrue(s1["gated"])
        self.assertEqual(s1["written"], 0)
        self.assertEqual(eng.longterm.count(), 0)
        eng.config.enabled = True
        s2 = eng.consolidate()
        self.assertGreaterEqual(s2["written"], 1)
        self.assertGreaterEqual(eng.longterm.count(), 1)

    def test_dedup_on_second_promotion(self):
        eng = self._engine(True)
        for _ in range(2):
            eng.observe("Perseus DDP training hangs on workstation two ranks cusparse spmm")
        eng.consolidate()
        n = eng.longterm.count()
        for _ in range(2):
            eng.observe("Perseus DDP training hangs on workstation two ranks cusparse spmm")
        s = eng.consolidate()
        self.assertEqual(eng.longterm.count(), n)  # deduped, no second copy
        self.assertGreaterEqual(s["duplicates"], 1)

    def test_recall_ranks_relevant_first_and_thin(self):
        eng = self._engine(True)
        for _ in range(2):
            eng.observe("Perseus training uses JAX flax nnx on the workstation")
        for _ in range(2):
            eng.observe("Evandor PSU board uses a buck converter for power")
        eng.consolidate()
        out = eng.recall("what does perseus training use")
        self.assertTrue(out["pointers"])
        self.assertIn("perseus", (out["pointers"][0]["name"] or ""))
        self.assertIn("[[", out["context"])

    def test_partial_adaptor_outcomes_backfilled(self):
        emb = HashingEmbedder()
        wm = WorkingMemory(":memory:", embedder=emb, clock=Clock())
        eng = Engine(wm, InMemoryLongTermStore(embedder=emb), EmptyAdaptor(),
                     EngineConfig(enabled=True, min_score=0.0))
        for _ in range(2):
            eng.observe("a recurring fact about something that repeats verbatim here")
        self.assertEqual(wm.pending_count(), 1)
        eng.consolidate()
        self.assertEqual(wm.pending_count(), 0)  # advanced despite empty adaptor outcomes


class TestMcpToolsRoundtrip(unittest.TestCase):
    def test_observe_consolidate_recall(self):
        emb = HashingEmbedder()
        eng = Engine(WorkingMemory(":memory:", embedder=emb, clock=Clock()),
                     InMemoryLongTermStore(embedder=emb),
                     MockAdaptor(), EngineConfig(enabled=True, min_score=0.0))
        mcp_tools.set_engine(eng)
        for _ in range(2):
            mcp_tools.tool_observe("Perseus training uses JAX flax nnx workstation", session="s1")
        c = mcp_tools.tool_consolidate()
        self.assertGreaterEqual(c["written"], 1)
        r = mcp_tools.tool_recall("perseus jax training")
        self.assertTrue(r["pointers"])
        self.assertTrue(mcp_tools.tool_status()["promotion_enabled"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
