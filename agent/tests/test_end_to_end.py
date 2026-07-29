# -*- coding: utf-8 -*-
"""End-to-end pipeline test against an ADVERSARIAL LLM stub.

The stub reproduces the three documented Groq misbehaviours (enum break,
dropped leaf, invented placeholder). The assertions prove the containment
machinery: coverage is total, silent loss is impossible, the meaning-breakage
cascade fires deterministically, and placeholders stay consistent everywhere.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.ingestion._contract import Leaf
from app.pipeline.inventory import merge_actors
from app.pipeline.surface_scan import scan
from app.pipeline.rules import apply_rules, load_rules
from app.pipeline.decide import reconcile_batch
from app.pipeline.validate_assemble import render_preview, validate_and_assemble
from app.llm.client import EnumViolation, closed_enum_call


# ── fixture document (the owner's canonical hard case, incl. the table row) ──

def make_leaves():
    rows = [
        ("title", "سياسة أمن المعلومات", "root", None, None),
        ("meta", "أعدّه: أحمد عبدالرحمن — مسؤول الاعتماد", "root", None, None),
        ("heading", "1. الغرض", "s1", None, None),
        ("paragraph", "تحدد هذه السياسة التزامات صندوق الاستثمارات العامة في حماية أصول المعلومات.", "s1", None, None),
        ("paragraph", "يلتزم الصندوق بمتطلبات الهيئة الوطنية للأمن السيبراني.", "s1", None, None),
        ("heading", "2. الاعتماد", "s2", None, None),
        ("table_cell", "قرار رقم 47", "s2", "t1r1", "القرار"),
        ("table_cell", "12/3/1445", "s2", "t1r1", "التاريخ"),
        ("table_cell", "اعتماد سياسة أمن المعلومات", "s2", "t1r1", "الموضوع"),
        ("table_cell", "أحمد عبدالرحمن", "s2", "t1r1", "المعتمد"),
        ("paragraph", "تُراجع هذه السياسة سنويًا من قبل إدارة الحوكمة بالصندوق.", "s2", None, None),
    ]
    return [Leaf(f"L_{i+1:06d}", *r) for i, r in enumerate(rows)]


SECTION_EXTRACTIONS = [
    [  # section 1 (Groq stub output)
        {"name": "أحمد عبدالرحمن", "kind": "PERSON", "role": "مسؤول الاعتماد",
         "variants": ["أحمد عبدالرحمن"]},
        {"name": "صندوق الاستثمارات العامة", "kind": "ORG_OWNER", "role": "الجهة المُصدِرة",
         "variants": ["صندوق الاستثمارات العامة", "الصندوق"]},
    ],
    [  # section 2 — same person again with a DIFFERENT role label
        {"name": "أحمد عبدالرحمن", "kind": "PERSON", "role": "المعتمد",
         "variants": ["أحمد عبدالرحمن"]},
        {"name": "إدارة الحوكمة", "kind": "ORG_UNIT", "role": "الوحدة المالكة للمراجعة",
         "variants": ["إدارة الحوكمة"]},
        {"name": "سياسة أمن المعلومات", "kind": "INTERNAL_DOC", "role": "الوثيقة نفسها",
         "variants": ["سياسة أمن المعلومات"]},
    ],
]


# ── stage 3b: merge locks ONE placeholder per actor ──────────────────────────

def test_merge_unifies_actors_and_locks_placeholders():
    actors = merge_actors(SECTION_EXTRACTIONS)
    assert len(actors) == 4  # أحمد merged across sections
    ahmed = next(a for a in actors.values() if a.name == "أحمد عبدالرحمن")
    assert ahmed.roles == ["مسؤول الاعتماد", "المعتمد"]     # both roles kept
    assert ahmed.placeholder == "<مسؤول_الاعتماد>"           # ONE placeholder
    assert len({a.placeholder for a in actors.values()}) == len(actors)  # no collisions


# ── stage 5: scan finds prefixed Arabic forms, offsets address original text ─

def test_scan_finds_prefixed_forms_with_correct_offsets():
    leaves = make_leaves()
    actors = merge_actors(SECTION_EXTRACTIONS)
    links = scan(leaves, actors)

    surfaces = {l.surface for l in links}
    assert "بالصندوق" in surfaces          # prefixed form ب+ال
    assert "الصندوق" in surfaces           # short variant
    assert "أحمد عبدالرحمن" in surfaces    # after diacritics in «أعدّه» — map holds

    for l in links:                        # every offset slices the ORIGINAL text
        leaf = next(x for x in leaves if x.leaf_id == l.leaf_id)
        assert leaf.text[l.start:l.end] == l.surface

    for leaf_id in {l.leaf_id for l in links}:  # no overlapping spans survive
        spans = sorted([(l.start, l.end) for l in links if l.leaf_id == leaf_id])
        for (s1, e1), (s2, e2) in zip(spans, spans[1:]):
            assert e1 <= s2


# ── stage 6: closed enum + automatic re-roll ─────────────────────────────────

class FlakyEnumClient:
    """First call breaks the enum (observed Groq behaviour); second complies."""
    def __init__(self):
        self.calls = 0

    def json_call(self, prompt, **kw):
        self.calls += 1
        bad_or_good = "DATE_THING" if self.calls == 1 else "QUALIFIER_OF_IDENTIFIER"
        return {"items": [
            {"surface": "قرار رقم 47", "class": "INSTANCE_IDENTIFIER"},
            {"surface": "12/3/1445", "class": bad_or_good},
        ]}


ENUM = {"INTERNAL_DOC_NAME", "INSTANCE_IDENTIFIER", "QUALIFIER_OF_IDENTIFIER",
        "PERSON", "ORG_OWNER", "ORG_UNIT", "ORG_EXTERNAL", "DOMAIN_TERM"}


def test_enum_violation_triggers_reroll():
    client = FlakyEnumClient()
    items = closed_enum_call(client, "classify", enum=ENUM, items_key="items")
    assert client.calls == 2                     # re-rolled exactly once
    assert all(i["class"] in ENUM for i in items)


def test_enum_violation_exhausts_and_raises():
    class AlwaysBad:
        def json_call(self, prompt, **kw):
            return {"items": [{"surface": "x", "class": "NOPE"}]}
    with pytest.raises(EnumViolation):
        closed_enum_call(AlwaysBad(), "classify", enum=ENUM, items_key="items", rerolls=1)


# ── stage 7: the meaning-breakage cascade (the owner's اختبار انكسار المعنى) ─

def test_cascade_hides_decision_number_and_date_with_doc_name():
    leaves = make_leaves()
    rules = load_rules()
    classifications = [
        {"leaf_id": "L_000007", "surface": "قرار رقم 47", "class": "INSTANCE_IDENTIFIER"},
        {"leaf_id": "L_000008", "surface": "12/3/1445", "class": "QUALIFIER_OF_IDENTIFIER"},
    ]
    hits = apply_rules(rules, leaves, hidden_rows={"t1r1"}, classifications=classifications)
    assert {h.leaf_id for h in hits} == {"L_000007", "L_000008"}
    assert {h.placeholder for h in hits} == {"<reference_number>", "<date>"}


# ── stage 8: reconciliation by leaf ID — silent loss impossible ──────────────

def test_reconcile_catches_dropped_leaf_and_invented_placeholder():
    sent = ["L_000001", "L_000002", "L_000005", "L_000011"]
    response = {
        "L_000001": {"decision": "REWRITE", "use": "<الوثيقة_نفسها>"},
        "L_000002": {"decision": "REWRITE", "use": "<مسؤول_الاعتماد>"},
        # L_000005 dropped by the model
        "L_000011": {"decision": "REWRITE", "use": "<منظمة_مخترعة>"},   # invented
    }
    allowed = {"<الوثيقة_نفسها>", "<مسؤول_الاعتماد>", "<الجهة_المُصدِرة>"}
    retries = []
    decisions = reconcile_batch(sent, response, allowed,
                                retry_fn=lambda i: retries.append(i) or None)
    by_id = {d.leaf_id: d for d in decisions}

    assert len(decisions) == len(sent)                      # every sent leaf answered
    assert retries == ["L_000005"]                          # single-leaf retry attempted
    assert by_id["L_000005"].decision == "REVIEW"           # visible, not lost
    assert by_id["L_000011"].decision == "REVIEW"           # invented → demoted
    assert "منظمة_مخترعة" in by_id["L_000011"].reason


# ── stage 9 + full flow: coverage gate, consistent output ────────────────────

def test_full_pipeline_end_to_end():
    leaves = make_leaves()
    actors = merge_actors(SECTION_EXTRACTIONS)
    links = scan(leaves, actors)
    allowed = {a.placeholder for a in actors.values()} | {"<reference_number>", "<date>"}

    linked_ids = sorted({l.leaf_id for l in links} | {"L_000007", "L_000008"})
    response = {i: {"decision": "REWRITE", "use": None} for i in linked_ids}
    del response["L_000005"]                                # adversarial drop
    decisions = reconcile_batch(linked_ids, response, allowed)
    keep_ids = {lf.leaf_id for lf in leaves} - set(linked_ids)
    from app.pipeline.decide.reconcile import Decision
    decisions += [Decision(i, "KEEP", reason="no linked surfaces") for i in sorted(keep_ids)]

    rules = load_rules()
    hits = apply_rules(rules, leaves, {"t1r1"}, [
        {"leaf_id": "L_000007", "surface": "قرار رقم 47", "class": "INSTANCE_IDENTIFIER"},
        {"leaf_id": "L_000008", "surface": "12/3/1445", "class": "QUALIFIER_OF_IDENTIFIER"},
    ])
    res = validate_and_assemble(leaves, links, decisions, actors, hits)

    assert res.metrics["coverage"] == 1.0
    assert res.metrics["silent_losses"] == 0
    assert res.metrics["review"] == 1                       # the dropped leaf, visible

    previews = {p["leaf_id"]: render_preview(
        next(l for l in leaves if l.leaf_id == p["leaf_id"]), p["spans"])
        for p in res.payload}
    assert previews["L_000010"] == "<مسؤول_الاعتماد>"
    assert previews["L_000002"] == "أعدّه: <مسؤول_الاعتماد> — مسؤول الاعتماد"
    assert previews["L_000007"] == "<reference_number>"
    assert previews["L_000008"] == "<date>"
    # the SAME person got the SAME placeholder in both places — consistency
    assert "<مسؤول_الاعتماد>" in previews["L_000002"] and "<مسؤول_الاعتماد>" in previews["L_000010"]
