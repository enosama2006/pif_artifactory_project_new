# -*- coding: utf-8 -*-
"""Regression tests for every finding of the first real-document run
(646d065f6ea4, PIF Data Governance Policy — see the diagnostics report).

Each test reproduces one observed defect and proves its fix.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingestion._contract import Leaf
from app.ingestion.ooxml.block import _drop_aggregate_duplicates, _PLACEHOLDER_TEXTS
from app.pipeline.candidates import sweep
from app.pipeline.decide import Decision
from app.pipeline.inventory.merge import Actor
from app.pipeline.surface_scan import scan
from app.pipeline.validate_assemble import (collapse_duplicate_placeholder,
                                            validate_and_assemble)


def make_actor(actor_id, name, kind, role, variants, placeholder):
    return Actor(actor_id=actor_id, name=name, kind=kind, roles=[role],
                 variants=variants, placeholder=placeholder)


# ── finding 4: digit-glued mention escaped the word-boundary guard ──────────

def test_scan_matches_after_digit_concatenation():
    leaves = [Leaf("L_000001", "paragraph",
                   "Data Governance Policy Chief of Staff April 2025Data Governance Policy",
                   "root")]
    actors = {"ACT_014": make_actor("ACT_014", "Data Governance Policy",
                                    "INTERNAL_DOC", "policy",
                                    ["Data Governance Policy"], "<Policy>")}
    hits = [l for l in scan(leaves, actors) if l.actor_id == "ACT_014"]
    assert len(hits) == 2                      # both mentions, incl. "2025Data…"
    assert {leaves[0].text[h.start:h.end] for h in hits} == {"Data Governance Policy"}


# ── finding 5: "April 2025" style dates now reach the classifier ────────────

def test_candidate_sweep_finds_month_year_dates():
    leaves = [Leaf("L_000001", "paragraph",
                   "Data Governance Policy — Chief of Staff April 2025", "root"),
              Leaf("L_000002", "paragraph", "صدر في 12 ربيع الأول 1447", "root")]
    hints = {c["surface"]: c["hint"] for c in sweep(leaves)}
    assert hints.get("April 2025") == "DATE"
    assert any("1447" in s for s, h in hints.items() if h == "DATE")


# ── definition sentences: "<X> (“<X>”)" folds to "<X>" ──────────────────────

def test_collapse_duplicate_placeholder():
    assert collapse_duplicate_placeholder(
        "The <owner> (“<owner>”) is the owner of this Policy."
    ) == "The <owner> is the owner of this Policy."
    assert collapse_duplicate_placeholder(
        "<Steering_Committee> (<Steering_Committee>) will prioritize."
    ) == "<Steering_Committee> will prioritize."
    # different placeholders must NOT fold
    assert "(<b>)" in collapse_duplicate_placeholder("<a> (<b>)")


# ── cover-page noise: placeholder prompts filtered, aggregates dropped ──────

def test_placeholder_prompt_text_is_known():
    assert "Click or tap here to enter text." in _PLACEHOLDER_TEXTS


def test_aggregate_duplicate_leaf_dropped():
    leaves = [
        Leaf("L_000001", "paragraph", "AlphaBetaGamma", "root"),
        Leaf("L_000002", "paragraph", "Alpha", "root"),
        Leaf("L_000003", "paragraph", "Beta", "root"),
        Leaf("L_000004", "paragraph", "Gamma", "root"),
        Leaf("L_000005", "paragraph", "Unrelated", "root"),
    ]
    _drop_aggregate_duplicates(leaves)
    assert [l.leaf_id for l in leaves] == ["L_000002", "L_000003", "L_000004", "L_000005"]


# ── critical: rewrites sharing one anchor are demoted, never applied ────────

def test_shared_anchor_rewrites_demoted_to_review():
    actors = {"A": make_actor("A", "Zeta Corp", "ORG_OWNER", "issuer",
                              ["Zeta Corp"], "<issuer>")}
    leaves = [Leaf("L_000001", "paragraph", "Zeta Corp title", "root", anchor="anz:C_1"),
              Leaf("L_000002", "paragraph", "Zeta Corp again", "root", anchor="anz:C_1"),
              Leaf("L_000003", "paragraph", "Zeta Corp alone", "root", anchor="anz:C_2")]
    links = scan(leaves, actors)
    decisions = [Decision(l.leaf_id, "REWRITE") for l in leaves]
    res = validate_and_assemble(leaves, links, decisions, actors)

    assert [p["leaf_id"] for p in res.payload] == ["L_000003"]   # unique anchor applies
    shared = [r for r in res.review_queue if "shared anchor" in r["reason"]]
    assert {r["leaf_id"] for r in shared} == {"L_000001", "L_000002"}


# ── findings 1-3: residual leak sweep catches missed variants ───────────────

def test_residual_acronym_leak_demotes_to_review():
    # inventory knows "Chief of Staff"/"CoS DH" but missed the "CoS Division" form
    actors = {"ACT_002": make_actor("ACT_002", "Chief of Staff", "PERSON", "owner",
                                    ["Chief of Staff", "CoS DH"], "<owner>")}
    leaves = [Leaf("L_000001", "paragraph",
                   "The Chief of Staff mandate aligns with CoS Division priorities.",
                   "root", anchor="anz:C_9")]
    links = scan(leaves, actors)                 # links only the known variants
    res = validate_and_assemble(leaves, links, [Decision("L_000001", "REWRITE")], actors)

    assert res.payload == []                     # not silently applied
    assert any("«CoS»" in r["reason"] for r in res.review_queue)


def test_residual_plain_token_warns_but_applies():
    # "Digital & Technology Department" known; bare "Digital & Technology" missed
    actors = {"ACT_006": make_actor("ACT_006", "Digital & Technology Department",
                                    "ORG_UNIT", "technology department",
                                    ["Digital & Technology Department"],
                                    "<technology_department>")}
    leaves = [Leaf("L_000001", "paragraph",
                   "The Digital & Technology Department leads, while Digital & "
                   "Technology owns the physical models.", "root", anchor="anz:C_7")]
    links = scan(leaves, actors)
    res = validate_and_assemble(leaves, links, [Decision("L_000001", "REWRITE")], actors)

    assert len(res.payload) == 1                 # applied (not blocked)
    assert any("Digital" in w or "Technology" in w for w in res.warnings)


def test_keep_leaf_with_acronym_token_flagged():
    actors = {"ACT_001": make_actor("ACT_001", "PIF", "ORG_OWNER", "organization",
                                    ["PIF"], "<organization>")}
    # scan will link PIF here — simulate an unlinked KEEP leaf via a variant miss
    leaves = [Leaf("L_000001", "paragraph",
                   "Hosted on the PIF-X shared platform.", "root")]
    res = validate_and_assemble(leaves, [], [Decision("L_000001", "KEEP")], actors)
    assert any("PIF" in r["reason"] for r in res.review_queue)
