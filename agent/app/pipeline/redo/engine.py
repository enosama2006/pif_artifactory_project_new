# -*- coding: utf-8 -*-
"""Comment-driven partial re-run (docs/DESIGN_hitl_comments.md).

One arbiter LLM call per comment turns it into a CLOSED operation; everything
after that is the same deterministic machinery as the initial run:
re-scan finds every sibling of a new surface (pure code), the cascade is
recomputed from the run's STORED classifications (zero extra LLM cost), and
only leaves whose mention structure actually changed — plus explicit
rewrite_leaf targets — go back to the decide LLM, carrying the user's comment
as binding guidance. Untouched leaves keep their decisions verbatim.
"""
import asyncio
import re
from dataclasses import asdict

from ..arbiter import build_arbiter_prompt, validate_op
from ..decide import Decision, reconcile_batch
from ..decide.prompt import build_decide_prompt
from ..inventory import Actor
from ..inventory.merge import mint_user_actor
from ..rules.engine import compute_cascade
from ..surface_scan import scan
from ..surface_scan.scan import normalize
from ..validate_assemble import validate_and_assemble


# ── comment → leaf resolution (deterministic, never guesses) ────────────────

def resolve_bind(bind: dict, leaves) -> dict:
    """{leaf_id|actor_id|anchor|paragraph_text|selected_text} → resolution.

    Priority: explicit leaf_id → anchor tag → normalized paragraph text →
    normalized selected-text containment (only when exactly ONE leaf
    matches). Ambiguity returns leaf_id=None with a note — visible, never a
    guess (the apply-fallback philosophy).
    """
    bind = bind or {}
    if bind.get("actor_id"):
        return {"leaf_id": None, "actor_id": bind["actor_id"], "how": "actor"}
    leaf_ids = {l.leaf_id for l in leaves}
    if bind.get("leaf_id"):
        if bind["leaf_id"] in leaf_ids:
            return {"leaf_id": bind["leaf_id"], "how": "explicit"}
        return {"leaf_id": None, "note": f"unknown leaf_id {bind['leaf_id']!r}"}

    selected = normalize(str(bind.get("selected_text", ""))).strip()

    if bind.get("anchor"):
        hits = [l for l in leaves if l.anchor == bind["anchor"]]
        if len(hits) > 1 and selected:   # shared anchor: the selection decides
            hits = [l for l in hits if selected in normalize(l.text)] or hits
        if hits:
            return {"leaf_id": hits[0].leaf_id, "how": "anchor"}

    para = normalize(str(bind.get("paragraph_text", ""))).strip()
    if para:
        hits = [l for l in leaves if normalize(l.text).strip() == para
                or para in normalize(l.text)]
        if len(hits) > 1 and selected:
            hits = [l for l in hits if selected in normalize(l.text)] or hits
        if len(hits) == 1:
            return {"leaf_id": hits[0].leaf_id, "how": "paragraph_text"}

    if selected:
        hits = [l for l in leaves if selected in normalize(l.text)]
        if len(hits) == 1:
            return {"leaf_id": hits[0].leaf_id, "how": "selected_text"}
        if hits:
            return {"leaf_id": None,
                    "note": f"selection matches {len(hits)} leaves — ambiguous"}

    return {"leaf_id": None, "note": "no leaf matched the binding"}


# ── dictionary mutations (pure code, one op at a time) ──────────────────────

def _amp_twins(surface: str) -> set[str]:
    out = {surface}
    if "&" in surface:
        out.add(re.sub(r"\s*&\s*", " and ", surface))
    elif re.search(r"\band\b", surface):
        out.add(re.sub(r"\s+and\s+", " & ", surface))
    return out


def _unique_placeholder(ph: str, actors: dict, own_id: str) -> str:
    taken = {a.placeholder for k, a in actors.items() if k != own_id}
    if ph not in taken:
        return ph
    stem = ph[1:-1]
    n = 2
    while f"<{stem}_{n}>" in taken:
        n += 1
    return f"<{stem}_{n}>"


def apply_op(op, actors: dict[str, Actor]) -> tuple[str, str]:
    """Execute one VALIDATED op on the dictionary. Returns (summary, error) —
    exactly one of them is non-empty. edit/rewrite/comment ops don't touch
    the dictionary and are handled by the caller."""
    f = op.fields
    if op.op == "add_surface":
        if "new_actor" in f:
            na = f["new_actor"]
            a = mint_user_actor(na["name"], na["kind"], na["role"],
                                sorted(_amp_twins(f["surface"])))
            n = len(actors) + 1
            while f"ACT_{n:03d}" in actors:
                n += 1
            a.actor_id = f"ACT_{n:03d}"
            a.placeholder = _unique_placeholder(a.placeholder, actors, a.actor_id)
            actors[a.actor_id] = a
            return (f"new actor {a.actor_id} «{a.name}» → {a.placeholder} "
                    f"with surface «{f['surface']}»"), ""
        a = actors[f["actor_id"]]
        a.variants = sorted(set(a.variants) | _amp_twins(f["surface"]))
        return f"surface «{f['surface']}» added to {a.name}", ""

    if op.op == "rename_placeholder":
        a = actors[f["actor_id"]]
        ph = _unique_placeholder(f["placeholder"], actors, a.actor_id)
        if ph != f["placeholder"]:
            return "", (f"rename_placeholder: {f['placeholder']} is already "
                        f"used by another actor")
        old, a.placeholder = a.placeholder, ph
        return f"{a.name}: {old} → {ph}", ""

    if op.op == "merge_actors":
        keep, drop = actors[f["keep"]], actors.pop(f["drop"])
        keep.variants = sorted(set(keep.variants) | set(drop.variants))
        for r in drop.roles:
            if r not in keep.roles:
                keep.roles.append(r)
        return f"{drop.name} merged into {keep.name} ({keep.placeholder})", ""

    if op.op == "correct_role":
        a = actors[f["actor_id"]]
        a.roles = [f["role"]] + [r for r in a.roles if r != f["role"]]
        return f"{a.name}: role → «{f['role']}»", ""

    if op.op == "ignore_actor":
        a = actors[f["actor_id"]]
        a.status = "ignored"
        return f"{a.name} ignored — its replacements dissolve", ""

    return f"{op.op} recorded", ""   # comment / edit_leaf / rewrite_leaf


# ── the partial re-run ───────────────────────────────────────────────────────

async def redo_run(result: dict, comments: list[dict], llm,
                   progress=lambda msg: None) -> dict:
    """result: the completed run's result dict (leaves/actors/links/decisions/
    classifications/cascade/portrait/payload). comments: pending comment
    records [{id, text, bind, resolved}]. Returns the REPLACEMENT result plus
    redo_report and updated_leaf_ids.
    """
    from app.ingestion._contract import Leaf
    from ..batching import build_skeleton
    from ..batching.plan import build_table_headers

    leaves = [Leaf(**d) for d in result["leaves"]]
    actors = {k: Actor(**v) for k, v in result["actors"].items()}
    leaf_by_id = {l.leaf_id: l for l in leaves}
    old_links = result.get("links", [])
    old_payload_by_leaf = {p["leaf_id"]: p for p in result.get("payload", [])}
    portrait = result.get("portrait") or {}

    # 1. arbiter — one CLOSED-output call per comment
    dictionary = {a.actor_id: {"name": a.name, "kind": a.kind,
                               "placeholder": a.placeholder,
                               "variants": a.variants, "roles": a.roles}
                  for a in actors.values()}

    def target_context(c):
        r = c.get("resolved") or {}
        out = {}
        if r.get("actor_id"):
            out["actor"] = dictionary.get(r["actor_id"])
        lid = r.get("leaf_id")
        if lid and lid in leaf_by_id:
            # linked_mentions is decisive for the arbiter's routing: identity
            # in the text but NOT in this list is a missed surface (HITL
            # run 1: "CoS DH" became toothless rewrite_leaf ops); the
            # actor_id/placeholder let a "wrong anonymising" complaint route
            # to rename_placeholder (HITL run 2).
            ph_of = {a.actor_id: a.placeholder for a in actors.values()}
            out["leaf"] = {
                "id": lid, "text": leaf_by_id[lid].text,
                "linked_mentions": sorted(
                    ({"surface": l["surface"], "actor_id": l["actor_id"],
                      "placeholder": ph_of.get(l["actor_id"])}
                     for l in old_links if l["leaf_id"] == lid),
                    key=lambda m: m["surface"])}
            if lid in old_payload_by_leaf:
                out["current_rewrite"] = old_payload_by_leaf[lid].get("after")
        return out

    async def arbitrate(c):
        payload = {"task": "arbiter", "text": c["text"], "bind": c.get("bind", {}),
                   "resolved": c.get("resolved", {}), "dictionary": dictionary,
                   "target": target_context(c), "portrait": portrait}
        try:
            return await asyncio.to_thread(
                llm.json_call, build_arbiter_prompt(payload), payload=payload)
        except Exception as exc:
            return {"_error": f"arbiter call failed: {exc!r}"}

    progress(f"redo: arbitrating {len(comments)} comment(s)")
    raw_ops = await asyncio.gather(*(arbitrate(c) for c in comments))

    # 2. validate + apply — invalid output is REPORTED, never executed
    report: list[dict] = []
    edit_overrides: dict[str, str] = {}
    guidance: dict[str, str] = {}          # leaf_id → binding user comment
    forced_redecide: set[str] = set()
    for c, raw in zip(comments, raw_ops):
        entry = {"comment_id": c.get("id"), "text": c["text"],
                 "resolved": c.get("resolved", {})}
        if isinstance(raw, dict) and "_error" in raw:
            entry["error"] = raw["_error"]
            report.append(entry)
            continue
        op, err = validate_op(raw, actors, set(leaf_by_id))
        if err:
            entry["error"] = err
            entry["raw"] = raw if isinstance(raw, dict) else str(raw)
            report.append(entry)
            continue
        entry["op"] = op.op
        entry["fields"] = op.fields
        entry["reason"] = op.reason
        if op.op == "edit_leaf":
            edit_overrides[op.fields["leaf_id"]] = op.fields["after"]
        elif op.op == "rewrite_leaf":
            forced_redecide.add(op.fields["leaf_id"])
            guidance[op.fields["leaf_id"]] = op.fields["guidance"]
        else:
            summary, err = apply_op(op, actors)
            if err:
                entry["error"] = err
                del entry["op"]
            else:
                entry["applied"] = summary
        # a bound leaf comment always reaches decide as guidance too
        lid = (c.get("resolved") or {}).get("leaf_id")
        if lid and lid not in guidance and "error" not in entry:
            guidance[lid] = c["text"]
        report.append(entry)

    # 3. deterministic re-scan + cascade (finds every sibling, zero LLM)
    active = {k: a for k, a in actors.items() if a.status != "ignored"}
    links = scan(leaves, active)
    cascade = compute_cascade(leaves, active, links,
                              result.get("classifications", []))
    progress(f"redo: re-scan {len(links)} links, cascade {len(cascade)}×")

    # 4. affected = mention structure changed ∪ forced rewrite targets
    def span_sets(link_dicts):
        out: dict[str, set] = {}
        for l in link_dicts:
            out.setdefault(l["leaf_id"], set()).add((l["start"], l["end"]))
        return out

    old_spans = span_sets(old_links)
    new_spans = span_sets([asdict(l) for l in links])
    affected = {lid for lid in set(old_spans) | set(new_spans)
                if old_spans.get(lid, set()) != new_spans.get(lid, set())}
    affected |= forced_redecide
    affected &= set(leaf_by_id)

    # 5. decide mini-batch — ONLY the affected leaves, guidance attached
    ph = {a.actor_id: a.placeholder for a in active.values()}
    allowed = set(ph.values()) | {c.placeholder for c in cascade}
    new_decisions: list[Decision] = []
    if affected:
        links_by_leaf: dict[str, list] = {}
        for l in links:
            links_by_leaf.setdefault(l.leaf_id, []).append(l)
        cascade_dicts = [asdict(h) for h in cascade]
        batch_ids = sorted(affected)
        payload = {
            "task": "decide", "skeleton": build_skeleton(leaves),
            "portrait": portrait, "batch_sections": ["redo"],
            "dictionary": {a.placeholder: {"name": a.name, "roles": a.roles}
                           for a in active.values()},
            "table_headers": build_table_headers(leaves),
            "user_guidance": {k: v for k, v in guidance.items() if k in affected},
            "leaves": [{
                "id": lid, "section": leaf_by_id[lid].section,
                "kind": leaf_by_id[lid].kind, "text": leaf_by_id[lid].text,
                "mentions": [{"surface": x.surface, "placeholder": ph[x.actor_id]}
                             for x in links_by_leaf.get(lid, [])],
                "cascade": [c for c in cascade_dicts if c["leaf_id"] == lid],
                **({"row": leaf_by_id[lid].row, "column": leaf_by_id[lid].col}
                   if leaf_by_id[lid].row else {}),
            } for lid in batch_ids],
        }
        progress(f"redo: deciding {len(batch_ids)} affected leaf/leaves")
        try:
            data = await asyncio.to_thread(
                llm.json_call, build_decide_prompt(payload), payload=payload)
            response = data.get("decisions", {}) if isinstance(data, dict) else {}
        except Exception as exc:
            response = {}
            progress(f"redo: decide call failed ({exc!r}) — affected leaves go to REVIEW")
        new_decisions = reconcile_batch(batch_ids, response, allowed)

    # 6. merge decisions (untouched leaves keep theirs verbatim) + assemble
    decisions = {d["leaf_id"]: Decision(**d) for d in result.get("decisions", [])}
    for d in new_decisions:
        decisions[d.leaf_id] = d
    for lf in leaves:
        decisions.setdefault(lf.leaf_id,
                             Decision(lf.leaf_id, "KEEP", reason="no linked surfaces"))
    res = validate_and_assemble(leaves, links, list(decisions.values()),
                                active, cascade)

    payload_by_leaf = {p["leaf_id"]: p for p in res.payload}

    def _override(lid, after, flag):
        lf = leaf_by_id[lid]
        item = payload_by_leaf.get(lid)
        if item is None:
            item = {"leaf_id": lid, "anchor": lf.anchor, "row": lf.row,
                    "column": lf.col, "before": lf.text, "after": after,
                    "spans": []}
            res.payload.append(item)
            res.payload.sort(key=lambda p: p["leaf_id"])
            payload_by_leaf[lid] = item
        else:
            item["after"] = after
            item["spans"] = []
        item[flag] = True
        res.review_queue = [r for r in res.review_queue if r["leaf_id"] != lid]

    # 7a. guided rewrites — spans cannot express a wording fix (HITL run 1:
    # "fix the duplicated employees" was obeyed in the decide REASON while
    # the span-rendered text kept the duplication). The rewrite text passed
    # the locked-dictionary check in reconcile; the leak gate runs here.
    from ..validate_assemble.validate import _actor_token_index, _leaks_in
    token_index = _actor_token_index(active)
    for d in new_decisions:
        if d.decision != "REWRITE" or not d.rewrite:
            continue
        if d.rewrite == leaf_by_id[d.leaf_id].text:
            continue
        leaks = [tok for tok, _aid, acro in _leaks_in(d.rewrite, token_index) if acro]
        if leaks:
            res.review_queue.append({
                "leaf_id": d.leaf_id, "text": leaf_by_id[d.leaf_id].text,
                "reason": f"guided rewrite still carries identity token "
                          f"«{leaks[0]}» — not applied"})
            continue
        _override(d.leaf_id, d.rewrite, "rewritten_by_guidance")

    # 7b. edit_leaf overrides — the human's wording is FINAL for those leaves
    for lid, after in edit_overrides.items():
        _override(lid, after, "edited_by_user")
    res.metrics["rewrites"] = len(res.payload)
    res.metrics["review"] = len(res.review_queue)

    # 8. what changed, card by card — the UI badges these
    new_payload_by_leaf = {p["leaf_id"]: p for p in res.payload}
    updated = set()
    for lid in set(old_payload_by_leaf) | set(new_payload_by_leaf):
        old_after = (old_payload_by_leaf.get(lid) or {}).get("after")
        new_after = (new_payload_by_leaf.get(lid) or {}).get("after")
        if old_after != new_after:
            updated.add(lid)
    old_review = {r["leaf_id"] for r in result.get("review_queue", [])}
    new_review = {r["leaf_id"] for r in res.review_queue}
    updated |= old_review ^ new_review

    # 9. per-comment EFFECT — "✓ op" with zero visible change misled the
    # owner (HITL run 1: three redos reported success, nothing moved).
    old_per_actor: dict[str, int] = {}
    for l in old_links:
        old_per_actor[l["actor_id"]] = old_per_actor.get(l["actor_id"], 0) + 1
    new_per_actor: dict[str, int] = {}
    for l in links:
        new_per_actor[l.actor_id] = new_per_actor.get(l.actor_id, 0) + 1
    for entry in report:
        if "error" in entry:
            continue
        f = entry.get("fields", {})
        if entry.get("op") in ("rewrite_leaf", "edit_leaf") \
                and f.get("leaf_id") not in updated:
            entry["warning"] = ("no visible change — the final text is identical; "
                                "if a surface was missed, ask to ADD it to the "
                                "dictionary instead of rewriting")
        if entry.get("op") == "add_surface":
            aid = f.get("actor_id") or next(
                (a.actor_id for a in actors.values()
                 if f.get("surface", "") in a.variants), None)
            if aid:
                entry["mentions_linked"] = (new_per_actor.get(aid, 0)
                                            - old_per_actor.get(aid, 0))

    return {"actors": {k: asdict(a) for k, a in actors.items()},
            "links": [asdict(l) for l in links],
            "cascade": [asdict(h) for h in cascade],
            "decisions": [asdict(d) for d in decisions.values()],
            "payload": res.payload, "review_queue": res.review_queue,
            "warnings": res.warnings, "metrics": res.metrics,
            "redo_report": report, "updated_leaf_ids": sorted(updated)}
