# -*- coding: utf-8 -*-
"""HITL comments + Redo (docs/DESIGN_hitl_comments.md).

Covers the owner's three real cases end-to-end against the redo engine with
a scripted arbiter/decide LLM, plus the closed-op gate and the deterministic
comment→leaf resolution. The LLM interprets, code executes — every test here
asserts that boundary.
"""
import asyncio
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingestion._contract import Leaf
from app.pipeline.arbiter import validate_op
from app.pipeline.decide import Decision
from app.pipeline.inventory.merge import Actor
from app.pipeline.redo import redo_run, resolve_bind
from app.pipeline.surface_scan import scan


def make_actor(actor_id, name, kind, role, variants, placeholder):
    return Actor(actor_id=actor_id, name=name, kind=kind, roles=[role],
                 variants=variants, placeholder=placeholder)


LEAVES = [
    Leaf("L_000001", "paragraph",
         "The Strategy Office reviews the policy annually.", "s1",
         anchor="anz:C_00001"),
    Leaf("L_000002", "paragraph",
         "Reports go to the Strategy Office and the Board.", "s1",
         anchor="anz:C_00002"),
    Leaf("L_000003", "table_cell", "Approved by the Board on 12/05/2024.",
         "s1", row="t1r1", col="Approval"),
]

BOARD = make_actor("ACT_001", "Board", "ORG_UNIT", "governing board",
                   ["Board"], "<governing_board>")


def build_result(leaves, actors):
    """A completed run's result dict, as routes.py stores it."""
    links = scan(leaves, actors)
    decisions = []
    for lf in leaves:
        linked = any(l.leaf_id == lf.leaf_id for l in links)
        decisions.append(Decision(lf.leaf_id, "REWRITE" if linked else "KEEP",
                                  reason="scripted"))
    from app.pipeline.validate_assemble import validate_and_assemble
    res = validate_and_assemble(leaves, links, decisions, actors)
    return {"leaves": [asdict(l) for l in leaves],
            "actors": {k: asdict(a) for k, a in actors.items()},
            "links": [asdict(l) for l in links],
            "decisions": [asdict(d) for d in decisions],
            "classifications": [], "cascade": [],
            "payload": res.payload, "review_queue": res.review_queue,
            "warnings": res.warnings, "metrics": res.metrics, "portrait": {}}


class ScriptedLlm:
    """task-keyed answers + call counting, like the other stage tests."""
    is_stub = False

    def __init__(self, arbiter=None, decide=None, revise=None):
        self.arbiter, self.decide, self.revise = arbiter or [], decide, revise
        self.calls = {"arbiter": 0, "decide": 0, "revise": 0}

    def json_call(self, prompt, *, payload=None, **kw):
        task = (payload or {}).get("task")
        if task == "arbiter":
            out = self.arbiter[self.calls["arbiter"]]
            self.calls["arbiter"] += 1
            return out
        if task == "decide":
            self.calls["decide"] += 1
            if callable(self.decide):
                return self.decide(payload)
            return self.decide or {"decisions": {
                l["id"]: {"decision": "REWRITE", "use": None, "reason": "ok"}
                for l in payload["leaves"]}}
        if task == "revise":
            self.calls["revise"] += 1
            if callable(self.revise):
                return self.revise(payload)
            return self.revise or {"rewrite": payload["current_rewrite"],
                                   "reason": "no change"}
        raise AssertionError(f"unexpected task {task!r}")


# ── closed-op gate ───────────────────────────────────────────────────────────

def test_validate_op_rejects_everything_outside_the_enum():
    actors = {"ACT_001": BOARD}
    op, err = validate_op({"op": "delete_document"}, actors, {"L_000001"})
    assert op is None and "closed set" in err
    op, err = validate_op({"op": "rename_placeholder", "actor_id": "ACT_009",
                           "placeholder": "<x>"}, actors, set())
    assert op is None and "unknown actor_id" in err
    op, err = validate_op({"op": "rename_placeholder", "actor_id": "ACT_001",
                           "placeholder": "Board!"}, actors, set())
    assert op is None and "snake_case" in err
    op, err = validate_op({"op": "edit_leaf", "leaf_id": "L_999999",
                           "after": "x"}, actors, {"L_000001"})
    assert op is None and "unknown leaf_id" in err


def test_validate_op_accepts_the_real_shapes():
    actors = {"ACT_001": BOARD}
    ok, err = validate_op({"op": "add_surface", "surface": "Strategy Office",
                           "new_actor": {"name": "Strategy Office",
                                         "kind": "ORG_UNIT",
                                         "role": "strategy office"}},
                          actors, set())
    assert err == "" and ok.fields["new_actor"]["kind"] == "ORG_UNIT"
    ok, err = validate_op({"op": "rewrite_leaf", "leaf_id": "L_000003",
                           "guidance": "generalize the date"},
                          actors, {"L_000003"})
    assert err == "" and ok.fields["guidance"] == "generalize the date"


# ── comment → leaf resolution ────────────────────────────────────────────────

def test_resolve_bind_prefers_anchor_then_text_and_never_guesses():
    r = resolve_bind({"anchor": "anz:C_00002"}, LEAVES)
    assert r["leaf_id"] == "L_000002" and r["how"] == "anchor"
    r = resolve_bind({"paragraph_text":
                      "The Strategy Office reviews the policy annually."}, LEAVES)
    assert r["leaf_id"] == "L_000001"
    # "Strategy Office" appears in two leaves → ambiguity is REPORTED
    r = resolve_bind({"selected_text": "Strategy Office"}, LEAVES)
    assert r["leaf_id"] is None and "ambiguous" in r["note"]
    # ...but the paragraph text disambiguates the same selection
    r = resolve_bind({"selected_text": "Strategy Office",
                      "paragraph_text": "Reports go to the Strategy Office and the Board."},
                     LEAVES)
    assert r["leaf_id"] == "L_000002"


# ── owner case 1: missed surface → siblings found, only they re-decided ─────

def test_case1_missed_surface_links_all_siblings_and_redecides_only_them():
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    # precondition: the missed unit survives the initial run untouched
    assert any("Strategy Office" in p["after"] for p in result["payload"])

    llm = ScriptedLlm(arbiter=[{
        "op": "add_surface", "surface": "Strategy Office",
        "new_actor": {"name": "Strategy Office", "kind": "ORG_UNIT",
                      "role": "strategy office"},
        "reason": "user selected a missed unit"}])
    comments = [{"id": "c1", "text": "this unit was missed — anonymize it",
                 "bind": {"anchor": "anz:C_00001",
                          "selected_text": "Strategy Office"},
                 "resolved": resolve_bind({"anchor": "anz:C_00001"}, LEAVES)}]
    delta = asyncio.run(redo_run(result, comments, llm))

    # the new actor exists and BOTH sibling leaves got linked by pure re-scan
    new = [a for a in delta["actors"].values() if a["name"] == "Strategy Office"]
    assert len(new) == 1
    linked = {l["leaf_id"] for l in delta["links"]
              if l["actor_id"] == new[0]["actor_id"]}
    assert linked == {"L_000001", "L_000002"}
    # only the leaves whose mention structure changed went back to the LLM
    assert llm.calls["decide"] == 1
    afters = {p["leaf_id"]: p["after"] for p in delta["payload"]}
    ph = new[0]["placeholder"]
    assert ph in afters["L_000001"] and ph in afters["L_000002"]
    assert set(delta["updated_leaf_ids"]) >= {"L_000001", "L_000002"}
    # the untouched cell kept its decision verbatim
    assert "L_000003" not in delta["updated_leaf_ids"]


# ── owner case 2: dictionary edit propagates with ZERO decide calls ─────────

def test_case2_rename_propagates_to_texts_without_llm():
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    llm = ScriptedLlm(arbiter=[{
        "op": "rename_placeholder", "actor_id": "ACT_001",
        "placeholder": "<board_of_directors>", "reason": "user prefers it"}])
    comments = [{"id": "c1", "text": "call it board_of_directors",
                 "bind": {"actor_id": "ACT_001"},
                 "resolved": {"actor_id": "ACT_001", "leaf_id": None}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    assert llm.calls["decide"] == 0            # mention structure unchanged
    afters = [p["after"] for p in delta["payload"]]
    assert any("<board_of_directors>" in a for a in afters)
    assert not any("<governing_board>" in a for a in afters)
    assert delta["actors"]["ACT_001"]["placeholder"] == "<board_of_directors>"


# ── owner case 3: unconvincing leaf redone by the dedicated reviser ─────────

def test_case3_rewrite_leaf_goes_to_the_reviser_and_changes_the_text():
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    seen = {}
    fixed = "Approved by <governing_board> under resolution <reference_number> dated <date>."

    def revise(payload):
        seen.update(payload)
        return {"rewrite": fixed, "reason": "per user comment"}

    llm = ScriptedLlm(arbiter=[{
        "op": "rewrite_leaf", "leaf_id": "L_000003",
        "guidance": "abstract the whole approval cell, not just the name",
        "reason": "user unconvinced"}], revise=revise)
    comments = [{"id": "c1", "text": "الصياغة غير مقنعة — أعد كتابة الخلية كاملة",
                 "bind": {"leaf_id": "L_000003"},
                 "resolved": {"leaf_id": "L_000003", "how": "explicit"}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    assert llm.calls["revise"] == 1
    assert llm.calls["decide"] == 0            # the reviser replaces decide here
    assert seen["comment"]                     # guidance reached the prompt
    assert "<reference_number>" in seen["allowed_placeholders"] \
           and "<date>" in seen["allowed_placeholders"]
    item = next(p for p in delta["payload"] if p["leaf_id"] == "L_000003")
    assert item["after"] == fixed
    assert item["rewritten_by_guidance"] is True
    assert "L_000003" in delta["updated_leaf_ids"]
    assert delta["redo_report"][0]["applied"] == "text revised per your comment"


# ── invalid arbiter output is shown, never executed ─────────────────────────

def test_invalid_arbiter_output_reported_not_executed():
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    llm = ScriptedLlm(arbiter=[{"op": "drop_all_tables", "reason": "nope"}])
    comments = [{"id": "c1", "text": "do something weird", "bind": {},
                 "resolved": {"leaf_id": None}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    assert llm.calls["decide"] == 0
    assert "error" in delta["redo_report"][0]
    assert delta["actors"].keys() == {"ACT_001"}     # dictionary untouched
    assert delta["updated_leaf_ids"] == []


# ── edit_leaf: the human's wording is final and clears the review flag ──────

def test_edit_leaf_overrides_payload_verbatim():
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    llm = ScriptedLlm(arbiter=[{
        "op": "edit_leaf", "leaf_id": "L_000003",
        "after": "Approved by <governing_board> on <date>.",
        "reason": "user dictated the wording"}])
    comments = [{"id": "c1",
                 "text": 'make it exactly "Approved by <governing_board> on <date>."',
                 "bind": {"leaf_id": "L_000003"},
                 "resolved": {"leaf_id": "L_000003", "how": "explicit"}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    item = next(p for p in delta["payload"] if p["leaf_id"] == "L_000003")
    assert item["after"] == "Approved by <governing_board> on <date>."
    assert item["edited_by_user"] is True
    assert "L_000003" in delta["updated_leaf_ids"]


# ── ignore_actor dissolves its rewrites ─────────────────────────────────────

def test_ignore_actor_dissolves_rewrites():
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    assert result["payload"]                    # Board rewrites exist initially
    llm = ScriptedLlm(arbiter=[{"op": "ignore_actor", "actor_id": "ACT_001",
                                "reason": "public body, keep it"}],
                      decide={"decisions": {}})
    comments = [{"id": "c1", "text": "Board must stay visible",
                 "bind": {"actor_id": "ACT_001"},
                 "resolved": {"actor_id": "ACT_001", "leaf_id": None}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    assert delta["actors"]["ACT_001"]["status"] == "ignored"
    assert all("<governing_board>" not in p["after"] for p in delta["payload"])


# ── the reviser: comment-driven text changes actually land ──────────────────
# (HITL runs 1-2: piggybacking the rewrite request on decide produced
# acknowledgments in the REASON while the text never changed)

def test_reviser_fixes_wording_and_is_reported():
    actors = {"ACT_001": BOARD}
    dup = Leaf("L_000010", "paragraph",
               "Reports go to the Board the Board for approval.", "s1",
               anchor="anz:C_00010")
    leaves = LEAVES + [dup]
    result = build_result(leaves, actors)

    fixed = "Reports go to <governing_board> for approval."
    llm = ScriptedLlm(arbiter=[{
        "op": "rewrite_leaf", "leaf_id": "L_000010",
        "guidance": "remove the duplicated Board mention",
        "reason": "user says duplicated"}],
        revise={"rewrite": fixed, "reason": "deduplicated"})
    comments = [{"id": "c1", "text": "fix it, the Board was duplicated",
                 "bind": {"leaf_id": "L_000010"},
                 "resolved": {"leaf_id": "L_000010", "how": "explicit"}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    item = next(p for p in delta["payload"] if p["leaf_id"] == "L_000010")
    assert item["after"] == fixed
    assert item["rewritten_by_guidance"] is True
    assert "L_000010" in delta["updated_leaf_ids"]
    assert "warning" not in delta["redo_report"][0]


def test_reviser_invented_placeholder_is_a_visible_error():
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    llm = ScriptedLlm(arbiter=[{
        "op": "rewrite_leaf", "leaf_id": "L_000003",
        "guidance": "reword it", "reason": "user asked"}],
        revise={"rewrite": "Approved by <made_up_tag> yesterday.",
                "reason": "reworded"})
    comments = [{"id": "c1", "text": "reword this cell",
                 "bind": {"leaf_id": "L_000003"},
                 "resolved": {"leaf_id": "L_000003", "how": "explicit"}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    # the invented tag never reaches the payload — and the WHY is visible
    assert not any("<made_up_tag>" in p["after"] for p in delta["payload"])
    assert "outside the locked dictionary" in delta["redo_report"][0]["error"]


def test_reviser_identical_text_gets_a_visible_warning():
    # HITL runs 1-2: redos reported "✓" while zero leaves changed — the
    # report must say so instead of implying success.
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    llm = ScriptedLlm(arbiter=[{
        "op": "rewrite_leaf", "leaf_id": "L_000003",
        "guidance": "anonymize the missing name",
        "reason": "user asked"}])   # revise default: echoes the current text
    comments = [{"id": "c1", "text": "this must be anonymized",
                 "bind": {"leaf_id": "L_000003"},
                 "resolved": {"leaf_id": "L_000003", "how": "explicit"}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    assert "identical" in delta["redo_report"][0]["warning"]


def test_add_surface_reports_mentions_linked():
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    llm = ScriptedLlm(arbiter=[{
        "op": "add_surface", "surface": "Strategy Office",
        "new_actor": {"name": "Strategy Office", "kind": "ORG_UNIT",
                      "role": "strategy office"},
        "reason": "missed unit"}])
    comments = [{"id": "c1", "text": "missed unit",
                 "bind": {"anchor": "anz:C_00001"},
                 "resolved": {"leaf_id": "L_000001", "how": "anchor"}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    assert delta["redo_report"][0]["mentions_linked"] == 2   # both siblings


# ── HITL run 2: the Reference cell's identifying fragments must be caught ───

def test_sweep_catches_hijri_date_and_parenthesized_resolution_number():
    from app.pipeline.candidates import sweep as run_sweep
    leaves = [Leaf("L_000037", "table_cell",
                   "The directive issued by the Saudi Authority for Data and "
                   "Artificial Intelligence under Cabinet Resolution number "
                   "(292) dating 27/04/1441H", "root",
                   row="t3r1", col="Reference")]
    surfaces = {c["surface"] for c in run_sweep(leaves)}
    assert "27/04/1441H" in surfaces           # Hijri date, H suffix
    assert any("(292)" in s for s in surfaces)  # parenthesized resolution no.


# ── HITL run 2: name-echoing placeholder + arbiter context for renames ──────

def test_three_word_name_restatement_never_becomes_the_placeholder():
    # Run abcd1b02f497: "National Data Management Office" minted
    # <data_management_office> — three name words back-to-back. Owner:
    # "wrong anonymising, must be abstracted".
    from app.pipeline.inventory import merge_actors
    actors = merge_actors([[
        {"name": "National Data Management Office", "kind": "ORG_EXTERNAL",
         "role": "data management office",
         "variants": ["NDMO", "National Data Management Office"]}]])
    a = next(iter(actors.values()))
    assert "data_management_office" not in a.placeholder


def test_arbiter_sees_actor_and_placeholder_of_linked_mentions():
    # A "wrong anonymising" complaint routes to rename_placeholder — the
    # arbiter needs the actor_id and current placeholder of the bound
    # leaf's mention in its context.
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    seen = {}

    class SpyLlm(ScriptedLlm):
        def json_call(self, prompt, *, payload=None, **kw):
            if (payload or {}).get("task") == "arbiter":
                seen.update(payload.get("target", {}))
            return super().json_call(prompt, payload=payload, **kw)

    llm = SpyLlm(arbiter=[{"op": "rename_placeholder", "actor_id": "ACT_001",
                           "placeholder": "<oversight_body>",
                           "reason": "user wants abstraction"}])
    comments = [{"id": "c1", "text": "wrong anonymising — abstract it",
                 "bind": {"leaf_id": "L_000002"},
                 "resolved": {"leaf_id": "L_000002", "how": "explicit"}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    mentions = seen["leaf"]["linked_mentions"]
    assert mentions and mentions[0]["actor_id"] == "ACT_001"
    assert mentions[0]["placeholder"] == "<governing_board>"
    # and the rename actually propagated
    assert delta["actors"]["ACT_001"]["placeholder"] == "<oversight_body>"
    assert any("<oversight_body>" in p["after"] for p in delta["payload"])


# ── HITL run 3: the exchange log, whole-row context, honest zero-link ───────
# Owner: "build a log that lets you see what was sent to the agent, what it
# returned, and what happened inside" — the trace IS that log, and it must
# survive into redo_report / redo_trace so diagnostics carries it.

def test_redo_report_carries_the_full_agent_trace():
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    raw_op = {"op": "rewrite_leaf", "leaf_id": "L_000003",
              "guidance": "abstract the whole cell", "reason": "user asked"}
    fixed = "Approved by <governing_board> on <date>."
    llm = ScriptedLlm(arbiter=[raw_op],
                      revise={"rewrite": fixed, "reason": "abstracted"})
    comments = [{"id": "c1", "text": "abstract this cell",
                 "bind": {"leaf_id": "L_000003"},
                 "resolved": {"leaf_id": "L_000003", "how": "explicit"}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    tr = delta["redo_report"][0]["trace"]
    # what was SENT to the arbiter…
    assert tr["arbiter"]["sent"]["text"] == "abstract this cell"
    assert tr["arbiter"]["sent"]["target"]["leaf"]["id"] == "L_000003"
    # …what it RETURNED…
    assert tr["arbiter"]["returned"] == raw_op
    # …and the reviser exchange that ran inside
    assert tr["reviser"]["sent"]["comment"] == "abstract the whole cell"
    assert tr["reviser"]["returned"]["rewrite"] == fixed


def test_decide_exchange_lands_in_redo_trace():
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    llm = ScriptedLlm(arbiter=[{
        "op": "add_surface", "surface": "Strategy Office",
        "new_actor": {"name": "Strategy Office", "kind": "ORG_UNIT",
                      "role": "strategy office"},
        "reason": "missed unit"}])
    comments = [{"id": "c1", "text": "missed unit",
                 "bind": {"anchor": "anz:C_00001"},
                 "resolved": {"leaf_id": "L_000001", "how": "anchor"}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    dec = delta["redo_trace"]["decide"]
    assert dec["sent"]["task"] == "decide"
    assert {l["id"] for l in dec["sent"]["leaves"]} == {"L_000001", "L_000002"}
    assert set(dec["returned"]["decisions"]) == {"L_000001", "L_000002"}


def test_table_cell_comment_ships_the_whole_row_and_can_target_a_sibling():
    # Run f6465516624b: HITL "works on texts but not table rows" — a comment
    # bound to one cell must let the arbiter see and target its siblings,
    # because the ROW is one record (iron rule 3).
    actors = {"ACT_001": BOARD}
    row = [Leaf("L_000021", "table_cell", "MC", "s2",
                row="t2r4", col="Code"),
           Leaf("L_000022", "table_cell", "Management Committee Charter", "s2",
                row="t2r4", col="Document"),
           Leaf("L_000023", "table_cell", "Kept by the Board.", "s2",
                row="t2r4", col="Notes")]
    leaves = LEAVES + row
    result = build_result(leaves, actors)
    seen_arbiter, seen_revise = {}, {}

    def revise(payload):
        seen_revise.update(payload)
        return {"rewrite": "Charter of the governance committee <redacted>",
                "reason": "abstracted per row context"}

    class SpyLlm(ScriptedLlm):
        def json_call(self, prompt, *, payload=None, **kw):
            if (payload or {}).get("task") == "arbiter":
                seen_arbiter.update(payload.get("target", {}))
            return super().json_call(prompt, payload=payload, **kw)

    # the comment is bound to the Code cell; the arbiter targets the SIBLING
    # Document cell — allowed because row_cells exposes its id
    llm = SpyLlm(arbiter=[{
        "op": "rewrite_leaf", "leaf_id": "L_000022",
        "guidance": "abstract the document title", "reason": "sibling cell"}],
        revise=revise)
    comments = [{"id": "c1", "text": "you missed the document in this row",
                 "bind": {"leaf_id": "L_000021"},
                 "resolved": {"leaf_id": "L_000021", "how": "explicit"}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    cells = seen_arbiter["row_cells"]
    assert [c["id"] for c in cells] == ["L_000021", "L_000022", "L_000023"]
    assert {c["column"] for c in cells} == {"Code", "Document", "Notes"}
    # the reviser also saw the row it is revising inside
    assert {c["column"] for c in seen_revise["row_cells"]} \
           == {"Code", "Document", "Notes"}
    item = next(p for p in delta["payload"] if p["leaf_id"] == "L_000022")
    assert item["after"] == "Charter of the governance committee <redacted>"
    assert "L_000022" in delta["updated_leaf_ids"]


def test_unmatched_surface_gets_zero_links_and_a_verbatim_warning():
    # Run f6465516624b: the arbiter invented «CoS division head» while the
    # document says "CoS DH" — zero mentions linked with no explanation.
    actors = {"ACT_001": BOARD}
    leaves = LEAVES + [Leaf("L_000030", "paragraph",
                            "The CoS DH signs the register quarterly.", "s1",
                            anchor="anz:C_00030")]
    result = build_result(leaves, actors)
    llm = ScriptedLlm(arbiter=[{
        "op": "add_surface", "surface": "CoS division head",
        "new_actor": {"name": "Chief of Staff division head",
                      "kind": "PERSON", "role": "division head"},
        "reason": "user says CoS is the chief of staff"}])
    comments = [{"id": "c1", "text": "CoS division head must be anonymised",
                 "bind": {"leaf_id": "L_000030"},
                 "resolved": {"leaf_id": "L_000030", "how": "explicit"}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    entry = delta["redo_report"][0]
    assert entry["mentions_linked"] == 0
    assert "verbatim" in entry["warning"]
    assert "CoS division head" in entry["warning"]


# ── API round-trip: comments accumulate, redo consumes them ─────────────────

def test_comments_api_roundtrip(monkeypatch):
    from fastapi.testclient import TestClient

    import app.llm as llm_mod
    from app.api.routes import RUNS, app as fastapi_app

    actors = {"ACT_001": BOARD}
    RUNS["testrun"] = {"run_id": "testrun", "status": "completed",
                       "current_stage": None, "stages": [],
                       "result": build_result(LEAVES, actors),
                       "comments": [], "processed_comments": []}
    scripted = ScriptedLlm(arbiter=[{
        "op": "rename_placeholder", "actor_id": "ACT_001",
        "placeholder": "<the_board>", "reason": "user asked"}])
    monkeypatch.setattr(llm_mod, "get_llm", lambda: scripted)

    client = TestClient(fastapi_app)
    r = client.post("/runs/testrun/comments", json={
        "text": "rename the Board placeholder",
        "bind": {"actor_id": "ACT_001"}}).json()
    assert r["ok"] and r["pending"] == 1
    assert r["comment"]["resolved"]["actor_id"] == "ACT_001"

    # a second comment can be removed before the redo
    r2 = client.post("/runs/testrun/comments", json={
        "text": "scratch this", "bind": {}}).json()
    assert r2["pending"] == 2
    rd = client.delete(f"/runs/testrun/comments/{r2['comment']['id']}").json()
    assert rd["ok"] and rd["pending"] == 1

    rr = client.post("/runs/testrun/redo").json()
    assert rr["ok"], rr
    assert rr["redo_report"][0]["applied"]
    assert RUNS["testrun"]["comments"] == []
    assert len(RUNS["testrun"]["processed_comments"]) == 1
    assert RUNS["testrun"]["result"]["actors"]["ACT_001"]["placeholder"] == "<the_board>"
    # redo with nothing pending is a visible no-op
    assert client.post("/runs/testrun/redo").json()["ok"] is False
