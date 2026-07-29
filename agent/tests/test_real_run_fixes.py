# -*- coding: utf-8 -*-
"""Regression tests for every finding of the first real-document run
(646d065f6ea4, PIF Data Governance Policy — see the diagnostics report).

Each test reproduces one observed defect and proves its fix.
"""
import re
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


# ── second real run (json_validate_failed): chunking + resilience ───────────

def test_inventory_chunks_split_giant_sections():
    from _adk.stages import _inventory_chunks
    leaves = [Leaf(f"L_{i:06d}", "paragraph", "x" * 500, "root") for i in range(1, 101)]
    chunks = list(_inventory_chunks(leaves))
    assert len(chunks) > 1                                  # 50k chars → split
    assert sum(len(c) for _, c in chunks) == 100            # nothing dropped
    assert all(sum(len(l.text) for l in c) <= 12000 for _, c in chunks)


def test_inventory_survives_partial_chunk_failure():
    import asyncio
    from _adk.stages import inventory_stage
    from app.llm.client import StubLlm

    class FlakyChunks(StubLlm):
        calls = 0
        def json_call(self, prompt, *, payload=None, **kw):
            FlakyChunks.calls += 1
            if FlakyChunks.calls == 1:
                raise RuntimeError("LLM call failed after retries: json_validate_failed")
            return {"actors": [{"name": "Zeta Corp", "kind": "ORG_OWNER",
                                "role": "issuer", "variants": ["Zeta Corp"]}]}

    leaves = [Leaf(f"L_{i:06d}", "paragraph", "y" * 500, "root") for i in range(1, 60)]
    state = {"leaves": [l.__dict__ for l in leaves]}
    result = asyncio.run(inventory_stage(state, FlakyChunks()))
    assert result["ok"] is True                             # run continues
    assert result["delta"]["inventory_failed_chunks"] == 1  # loss is visible
    assert "ACT_001" in result["delta"]["actors"]           # partial inventory kept


def test_outline_level_marks_heading():
    import io, zipfile
    from app.ingestion import ingest
    W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    doc = (f'<w:document {W_NS}><w:body>'
           '<w:p><w:pPr><w:pStyle w:val="PIFCustomHead"/><w:outlineLvl w:val="0"/></w:pPr>'
           '<w:r><w:t>Purpose</w:t></w:r></w:p>'
           '<w:p><w:r><w:t>Body text here.</w:t></w:r></w:p>'
           '</w:body></w:document>')
    usd = ingest(doc.encode(), "ooxml")
    assert [l.kind for l in usd.leaves] == ["heading", "paragraph"]
    assert usd.leaves[1].section == "s1"                    # section boundary created


# ── third real run (3e6163a5156e): inventory fragmentation fixes ────────────

def test_merge_unifies_article_prefix_and_shared_variant():
    from app.pipeline.inventory import merge_actors
    extractions = [
        [{"name": "Data and Analytics Steering Committee", "kind": "ORG_UNIT",
          "role": "steering committee", "variants": ["DASC", "Data and Analytics Steering Committee"]}],
        [{"name": "The Data and Analytics Steering Committee", "kind": "ORG_UNIT",
          "role": "steering committee", "variants": ["The Data and Analytics Steering Committee", "DASC"]}],
        [{"name": "Chief of Staff", "kind": "PERSON", "role": "policy owner",
          "variants": ["Chief of Staff", "CoS DH"]}],
        [{"name": "CoS DH", "kind": "PERSON",
          "role": "Chief of Staff, Department of Health",     # hallucinated expansion
          "variants": ["CoS DH", "the CoS DH"]}],
    ]
    actors = merge_actors(extractions)
    assert len(actors) == 2                       # one committee, one person
    person = next(a for a in actors.values() if "CoS DH" in a.variants)
    assert person.roles[0] == "policy owner"      # first-seen role wins over hallucination


def test_variant_trimming_drops_wrapping_phrases():
    from app.pipeline.inventory import merge_actors
    actors = merge_actors([[
        {"name": "PIF", "kind": "ORG_OWNER", "role": "owner organisation",
         "variants": ["PIF", "PIF data systems", "PIF premises", "PIF-wide",
                      "throughout PIF", "PIF’s"]},
    ]])
    a = next(iter(actors.values()))
    assert a.variants == ["PIF"]                  # phrases trimmed; core matches inside them


def test_generic_pseudo_actors_dropped():
    from app.pipeline.inventory import merge_actors
    actors = merge_actors([[
        {"name": "This Policy", "kind": "INTERNAL_DOC", "role": "policy document",
         "variants": ["Policy", "This Policy", "the Policy"]},
        {"name": "Data Strategy", "kind": "INTERNAL_DOC", "role": "data strategy",
         "variants": ["Data Strategy"]},
        {"name": "Change Management Plan", "kind": "INTERNAL_DOC",
         "role": "change management plan", "variants": ["Change Management Plan"]},
        {"name": "Zeta Corp", "kind": "ORG_OWNER", "role": "owner organisation",
         "variants": ["Zeta Corp"]},
    ]])
    names = {a.name for a in actors.values()}
    assert names == {"Zeta Corp"}                 # only the identity-bearing actor survives


def test_placeholder_never_contains_identity_tokens():
    from app.pipeline.inventory import merge_actors
    actors = merge_actors([[
        {"name": "PIF", "kind": "ORG_OWNER", "role": "owner organisation",
         "variants": ["PIF"]},
        {"name": "PIF Academy", "kind": "ORG_UNIT", "role": "PIF training academy",
         "variants": ["PIF Academy"]},
    ]])
    for a in actors.values():
        assert "PIF" not in a.placeholder         # identity stripped from the tag
        assert re.match(r"^<[\w؀-ۿ_]+>$", a.placeholder)  # clean charset


def test_long_outline_paragraph_stays_paragraph():
    import io, zipfile
    from app.ingestion import ingest
    W_NS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    long_clause = "The Department shall " + "do many things " * 20   # ≫100 chars
    doc = (f'<w:document {W_NS}><w:body>'
           '<w:p><w:pPr><w:outlineLvl w:val="0"/></w:pPr>'
           '<w:r><w:t>Purpose</w:t></w:r></w:p>'
           f'<w:p><w:pPr><w:outlineLvl w:val="1"/></w:pPr>'
           f'<w:r><w:t>{long_clause}</w:t></w:r></w:p>'
           '</w:body></w:document>')
    usd = ingest(doc.encode(), "ooxml")
    assert [l.kind for l in usd.leaves] == ["heading", "paragraph"]


# ── fourth real run (72d2c2e3b84a): role/placeholder quality ────────────────

def test_role_picking_prefers_function_over_name_echo():
    from app.pipeline.inventory import merge_actors
    actors = merge_actors([[
        {"name": "Digital & Technology Department", "kind": "ORG_UNIT",
         "role": "Digital & Technology Department",          # echoes the name
         "variants": ["D&T", "Digital & Technology"]},
    ], [
        {"name": "Digital & Technology Department", "kind": "ORG_UNIT",
         "role": "IT department",                            # the function
         "variants": ["Digital & Technology Department"]},
    ]])
    a = next(iter(actors.values()))
    assert a.placeholder == "<IT_department>"                # function wins


def test_husk_placeholder_falls_back_to_kind():
    from app.pipeline.inventory import merge_actors
    actors = merge_actors([[
        {"name": "Saudi Authority for Data and Artificial Intelligence",
         "kind": "ORG_EXTERNAL",
         "role": "Saudi Authority for Data and Artificial Intelligence",
         "variants": ["Saudi Authority for Data and Artificial Intelligence"]},
    ]])
    a = next(iter(actors.values()))
    assert a.placeholder == "<external_authority>"           # no <for_Data_and_...> husk


def test_duplicate_tail_word_collapses():
    assert collapse_duplicate_placeholder(
        "The <governance_department> Department shall lead."
    ) == "The <governance_department> shall lead."
    # unrelated following word must survive
    assert collapse_duplicate_placeholder(
        "The <governance_department> leads.") == "The <governance_department> leads."


def test_payload_carries_row_for_block_apply():
    actors = {"A": make_actor("A", "Zeta Corp", "ORG_OWNER", "issuer",
                              ["Zeta Corp"], "<issuer>")}
    leaves = [Leaf("L_000001", "table_cell", "Zeta Corp", "s1",
                   row="t1r1", col="Name", anchor="anz:C_1")]
    links = scan(leaves, actors)
    res = validate_and_assemble(leaves, links, [Decision("L_000001", "REWRITE")], actors)
    assert res.payload[0]["row"] == "t1r1"
    assert res.payload[0]["column"] == "Name"


# ── fifth real run (a30d8030eb59): placeholder quality + false REVIEWs ──────

def test_role_ranking_prefers_concise_consensus_over_longest():
    # PIF got <data_training_participants>: the old score preferred the
    # LONGEST identity-free role. Two-word functional roles must win.
    from app.pipeline.inventory import merge_actors
    actors = merge_actors([[
        {"name": "PIF", "kind": "ORG_OWNER", "role": "owner organization",
         "variants": ["PIF", "Public Investment Fund"]},
    ], [
        {"name": "PIF", "kind": "ORG_OWNER", "role": "data training participants",
         "variants": ["PIF"]},
    ]])
    a = next(iter(actors.values()))
    assert a.placeholder == "<owner_organization>"


def test_title_person_keeps_title_placeholder():
    # CDO/PDPO got <person>/<person_2>: a job title carries no personal
    # identity — the title itself is the correct placeholder.
    from app.pipeline.inventory import merge_actors
    actors = merge_actors([[
        {"name": "Chief Data Officer (CDO)", "kind": "PERSON",
         "role": "chief data officer",
         "variants": ["Chief Data Officer (CDO)", "CDO"]},
        {"name": "Personal Data Protection Officer (PDPO)", "kind": "PERSON",
         "role": "personal data protection officer",
         "variants": ["Personal Data Protection Officer (PDPO)", "PDPO"]},
    ]])
    phs = sorted(a.placeholder for a in actors.values())
    assert phs == ["<chief_data_officer>", "<personal_data_protection_officer>"]


def test_function_word_units_avoid_kind_fallback_flood():
    # Seven units collapsed into <organisational_unit>.._7 because function
    # vocabulary (technology, cybersecurity, …) counted as identity.
    from app.pipeline.inventory import merge_actors
    actors = merge_actors([[
        {"name": "Digital & Technology Department", "kind": "ORG_UNIT",
         "role": "technology department",
         "variants": ["D&T", "Digital & Technology Department"]},
        {"name": "Cybersecurity Risk Department", "kind": "ORG_UNIT",
         "role": "cybersecurity department",
         "variants": ["Cybersecurity Risk Department"]},
        {"name": "Data and Committees Governance and Advisory Department",
         "kind": "ORG_UNIT", "role": "data governance department",
         "variants": ["DCGA", "Data and Committees Governance and Advisory Department"]},
    ]])
    phs = {a.name: a.placeholder for a in actors.values()}
    assert phs["Digital & Technology Department"] == "<technology_department>"
    assert phs["Cybersecurity Risk Department"] == "<cybersecurity_department>"
    assert (phs["Data and Committees Governance and Advisory Department"]
            == "<data_governance_department>")
    assert not any(p.startswith("<organisational_unit") for p in phs.values())


def test_ampersand_name_variants_merge_to_one_actor():
    # "Records & Administration…" and "Records and Administration…" became
    # TWO actors with two placeholders — "&" must fold to "and" in the key.
    from app.pipeline.inventory import merge_actors
    actors = merge_actors([[
        {"name": "Records & Administration Center Department", "kind": "ORG_UNIT",
         "role": "records administration department",
         "variants": ["Records & Administration Center Department", "RAC"]},
    ], [
        {"name": "Records and Administration Center Department", "kind": "ORG_UNIT",
         "role": "records management department",
         "variants": ["Records and Administration Center Department"]},
    ]])
    assert len(actors) == 1


def test_parenthetical_variant_splits_for_scan():
    # "Chief Data Officer" hit REVIEW as a missed surface: the only variants
    # were "Chief Data Officer (CDO)" and "CDO" — the bare title never matched.
    from app.pipeline.inventory import merge_actors
    actors = merge_actors([[
        {"name": "Chief Data Officer (CDO)", "kind": "PERSON",
         "role": "chief data officer", "variants": ["Chief Data Officer (CDO)"]},
    ]])
    leaves = [Leaf("L_000001", "paragraph",
                   "The Chief Data Officer approves this framework.", "s1")]
    links = scan(leaves, actors)
    assert any(l.surface == "Chief Data Officer" for l in links)


def test_reconcile_tolerates_comma_joined_valid_placeholders():
    # 4 false REVIEWs: "use" came back as "<a>, <b>" (all in the dictionary).
    # Advisory multi-use must pass through (spans drive the rewrite); a
    # genuinely invented placeholder must still demote to REVIEW.
    from app.pipeline.decide import reconcile_batch
    allowed = {"<organisational_unit>", "<technology_department>"}
    out = reconcile_batch(
        ["L_1", "L_2"],
        {"L_1": {"decision": "REWRITE",
                 "use": "<organisational_unit>, <technology_department>"},
         "L_2": {"decision": "REWRITE", "use": "<made_up_tag>"}},
        allowed)
    assert out[0].decision == "REWRITE" and out[0].placeholder is None
    assert out[1].decision == "REVIEW"


def test_portrait_hint_biases_placeholder():
    # The portrait's document-level view of an actor's function must win
    # placeholder minting over a stray per-chunk role.
    from app.pipeline.inventory import merge_actors
    actors = merge_actors(
        [[{"name": "PIF", "kind": "ORG_OWNER",
           "role": "data training participants", "variants": ["PIF"]}]],
        portrait={"actors": [{"name": "PIF", "function": "owner organisation"}]})
    a = next(iter(actors.values()))
    assert a.placeholder == "<owner_organisation>"


def test_generic_variant_pollution_does_not_merge_departments():
    # Run-5 latent bug: the LLM attached the all-generic phrase
    # "Advanced Analytics & AI" to the D&T department's variants; a shared
    # generic variant must NOT merge two real departments, and the polluted
    # variant must be dropped from the unrelated actor.
    from app.pipeline.inventory import merge_actors
    actors = merge_actors([[
        {"name": "Digital & Technology Department", "kind": "ORG_UNIT",
         "role": "technology department",
         "variants": ["D&T", "Digital & Technology", "Advanced Analytics & AI"]},
    ], [
        {"name": "Advanced Analytics & AI Department", "kind": "ORG_UNIT",
         "role": "advanced analytics department",
         "variants": ["AIAA", "Advanced Analytics & AI"]},
    ]])
    assert len(actors) == 2
    dt = next(a for a in actors.values() if a.name.startswith("Digital"))
    assert "Advanced Analytics & AI" not in dt.variants
    aiaa = next(a for a in actors.values() if a.name.startswith("Advanced"))
    assert "Advanced Analytics & AI" in aiaa.variants   # related → kept
