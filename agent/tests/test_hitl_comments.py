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

    def __init__(self, arbiter=None, decide=None):
        self.arbiter, self.decide = arbiter or [], decide
        self.calls = {"arbiter": 0, "decide": 0}

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


# ── owner case 3: unconvincing leaf redone with the comment as guidance ─────

def test_case3_rewrite_leaf_carries_the_user_guidance_into_decide():
    actors = {"ACT_001": BOARD}
    result = build_result(LEAVES, actors)
    seen = {}

    def decide(payload):
        seen.update(payload.get("user_guidance") or {})
        return {"decisions": {l["id"]: {"decision": "REVIEW", "use": None,
                                        "reason": "per user comment"}
                              for l in payload["leaves"]}}

    llm = ScriptedLlm(arbiter=[{
        "op": "rewrite_leaf", "leaf_id": "L_000003",
        "guidance": "replace the whole approval sentence, not just the name",
        "reason": "user unconvinced"}], decide=decide)
    comments = [{"id": "c1", "text": "الصياغة غير مقنعة — أعد كتابة الخلية كاملة",
                 "bind": {"leaf_id": "L_000003"},
                 "resolved": {"leaf_id": "L_000003", "how": "explicit"}}]
    delta = asyncio.run(redo_run(result, comments, llm))
    assert llm.calls["decide"] == 1
    assert "L_000003" in seen                  # guidance reached the prompt
    assert any(r["leaf_id"] == "L_000003" for r in delta["review_queue"])
    assert "L_000003" in delta["updated_leaf_ids"]


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
