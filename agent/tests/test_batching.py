# -*- coding: utf-8 -*-
"""Structure/text separation: skeleton as context, full-coverage batching.

Owner's invariant: every leaf lands in exactly one batch; the sum of leaves
across batches equals the tree total — verified by plan_batches itself and
re-asserted end-to-end here.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.ingestion._contract import Leaf
from app.pipeline.batching import build_skeleton, plan_batches
from app.pipeline.batching.plan import Batch
from app.llm.client import StubLlm
from _adk.stages import decide_stage


def make_doc(n_sections=5, leaves_per_section=7):
    leaves, n = [], 0
    for s in range(1, n_sections + 1):
        n += 1
        leaves.append(Leaf(f"L_{n:06d}", "heading", f"Section {s}", f"s{s}"))
        for _ in range(leaves_per_section - 1):
            n += 1
            leaves.append(Leaf(f"L_{n:06d}", "paragraph", "text " * 30, f"s{s}"))
    return leaves


def test_plan_covers_every_leaf_exactly_once():
    leaves = make_doc()
    batches = plan_batches(leaves)
    placed = [i for b in batches for i in b.leaf_ids]
    assert len(placed) == len(leaves)                 # sum(batches) == tree total
    assert set(placed) == {l.leaf_id for l in leaves}
    assert len(batches) > 1
    assert all(len(b.leaf_ids) <= 10 for b in batches)


def test_batches_prefer_section_edges():
    leaves = make_doc(n_sections=4, leaves_per_section=8)
    batches = plan_batches(leaves)
    # most batches should not straddle many sections
    assert sum(1 for b in batches if len(b.sections) == 1) >= len(batches) // 2


def test_skeleton_lists_headings_only():
    leaves = make_doc(n_sections=3, leaves_per_section=5)
    sk = build_skeleton(leaves)
    assert [e["text"] for e in sk] == ["Section 1", "Section 2", "Section 3"]


def test_decide_stage_covers_all_leaves_and_passes_skeleton():
    leaves = make_doc(n_sections=3, leaves_per_section=6)
    seen_payloads = []

    class SpyStub(StubLlm):
        def json_call(self, prompt, *, payload=None, **kw):
            if (payload or {}).get("task") == "decide":
                seen_payloads.append(payload)
            return super().json_call(prompt, payload=payload, **kw)

    state = {"leaves": [l.__dict__ for l in leaves], "actors": {},
             "links": [], "cascade": []}
    result = asyncio.run(decide_stage(state, SpyStub()))
    assert result["ok"] is True
    assert len(result["delta"]["decisions"]) == len(leaves)   # full coverage
    assert result["delta"]["batch_count"] == len(seen_payloads) > 1
    for p in seen_payloads:                                    # structure rides along
        assert p["skeleton"] and p["batch_sections"]
    decided_ids = {d["leaf_id"] for d in result["delta"]["decisions"]}
    assert decided_ids == {l.leaf_id for l in leaves}


def test_plan_edge_cases():
    assert plan_batches([]) == []                          # empty doc -> no batches
    one = [Leaf("L_000001", "paragraph", "only leaf", "root")]
    batches = plan_batches(one)
    assert [b.leaf_ids for b in batches] == [["L_000001"]]
    huge = [Leaf("L_000001", "paragraph", "x" * 20000, "root")]  # > char budget
    assert plan_batches(huge)[0].leaf_ids == ["L_000001"]  # oversized leaf still placed
