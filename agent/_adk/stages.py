# -*- coding: utf-8 -*-
"""Pipeline stages — pure async functions, testable without any ADK runtime.

Contract: `stage(state: dict, llm) -> {"ok": bool, "message": str, "delta": dict}`.
State values are JSON-serializable (dicts/lists) so they survive ADK session
persistence; stages rebuild dataclasses at the edges.

Who does what (docs/DESIGN_pipeline.md): the LLM appears ONLY in
inventory (extract actors per section), classify (closed enum) and decide
(REWRITE/KEEP/REVIEW per leaf). Everything else is deterministic code, and
every LLM answer is validated — enum re-roll, leaf-ID reconciliation,
locked-dictionary check.
"""
from dataclasses import asdict
from pathlib import Path

from app.ingestion import ingest
from app.ingestion._contract import Leaf
from app.llm.client import EnumViolation, closed_enum_call
from app.pipeline.candidates import sweep
from app.pipeline.decide import Decision, reconcile_batch
from app.pipeline.decide.prompt import build_decide_prompt, build_retry_prompt
from app.pipeline.inventory import Actor, merge_actors
from app.pipeline.inventory.prompt import CLASS_ENUM, build_classify_prompt, build_inventory_prompt
from app.pipeline.rules import apply_rules, load_rules
from app.pipeline.rules.engine import CascadeHit
from app.pipeline.surface_scan import scan
from app.pipeline.surface_scan.scan import SurfaceLink
from app.pipeline.validate_assemble import validate_and_assemble


def _leaves(state) -> list[Leaf]:
    return [Leaf(**d) for d in state["leaves"]]


def _actors(state) -> dict[str, Actor]:
    return {k: Actor(**v) for k, v in state.get("actors", {}).items()}


def _links(state) -> list[SurfaceLink]:
    return [SurfaceLink(**d) for d in state.get("links", [])]


# ── 1. ingest ────────────────────────────────────────────────────────────────

async def ingest_stage(state, llm):
    raw = Path(state["input_path"]).read_bytes()
    fmt = "docx" if raw[:4] == b"PK\x03\x04" else "ooxml"
    usd = ingest(raw, fmt)
    if not usd.leaves:
        return {"ok": False, "message": "no text leaves found in the document"}
    kinds: dict[str, int] = {}
    for lf in usd.leaves:
        kinds[lf.kind] = kinds.get(lf.kind, 0) + 1
    return {"ok": True,
            "message": f"{usd.leaf_count} leaves ({kinds}) — coverage invariant set",
            "delta": {"leaves": [asdict(l) for l in usd.leaves],
                      "leaf_count": usd.leaf_count}}


# ── 2. inventory (LLM per section) + deterministic merge, dictionary LOCKED ─

def _inventory_chunks(leaves):
    """Section-bounded chunks, sub-split by char budget — one huge 'root'
    section (a doc whose headings the parser can't classify) must not become
    one giant LLM call (real-run finding: json_validate_failed on 124 leaves)."""
    from app import config
    sections: dict[str, list[Leaf]] = {}
    for lf in leaves:
        sections.setdefault(lf.section, []).append(lf)
    for sec, sec_leaves in sections.items():
        chunk, size = [], 0
        for lf in sec_leaves:
            if chunk and size + len(lf.text) > config.INVENTORY_CHUNK_CHARS:
                yield sec, chunk
                chunk, size = [], 0
            chunk.append(lf)
            size += len(lf.text)
        if chunk:
            yield sec, chunk


async def inventory_stage(state, llm):
    from app import config
    leaves = _leaves(state)
    extractions, failed = [], 0
    chunks = list(_inventory_chunks(leaves))
    for sec, chunk in chunks:
        payload = {"task": "inventory", "section": sec,
                   "leaves": [{"id": l.leaf_id, "kind": l.kind, "text": l.text}
                              for l in chunk]}
        try:
            data = llm.json_call(build_inventory_prompt(payload), payload=payload,
                                 max_tokens=config.INVENTORY_MAX_TOKENS)
            extractions.append([a for a in data.get("actors", []) if isinstance(a, dict)])
        except Exception:  # one chunk must not kill the run — the residual
            failed += 1    # leak sweep + review queue compensate downstream
    if failed == len(chunks) and chunks:
        return {"ok": False,
                "message": f"inventory failed on all {failed} chunk(s) — aborting"}

    actors = merge_actors(extractions)
    msg = (f"{len(actors)} actors from {len(chunks) - failed}/{len(chunks)} chunk(s)"
           + (f" ({failed} chunk(s) FAILED — coverage reduced, check review queue)"
              if failed else "")
           + ("; dictionary LOCKED: "
              + ", ".join(f"{a.name}→{a.placeholder}" for a in actors.values())
              if actors else
              ("" if failed else
               " (stub mode — set GROQ_API_KEY for real extraction)" if llm.is_stub else "")))
    return {"ok": True, "message": msg,
            "delta": {"actors": {k: asdict(a) for k, a in actors.items()},
                      "inventory_failed_chunks": failed}}


# ── 3. surface scan (deterministic — the no-drop net) ───────────────────────

async def scan_stage(state, llm):
    links = scan(_leaves(state), _actors(state))
    return {"ok": True, "message": f"{len(links)} surface links",
            "delta": {"links": [asdict(l) for l in links]}}


# ── 4. candidates + closed-enum classify + breakage cascade ─────────────────

async def classify_rules_stage(state, llm):
    leaves = _leaves(state)
    candidates = sweep(leaves)
    classifications = []
    if candidates:
        payload = {"task": "classify", "items": candidates}
        try:
            items = closed_enum_call(llm, build_classify_prompt(payload),
                                     enum=CLASS_ENUM, items_key="items",
                                     payload=payload)
        except EnumViolation:
            items = []  # unclassifiable → they simply never join a cascade
        by_surface = {c["surface"]: c for c in candidates}
        for it in items:
            src = by_surface.get(it.get("surface"))
            if src:
                classifications.append({"leaf_id": src["leaf_id"],
                                        "surface": src["surface"],
                                        "class": it["class"]})

    actors = _actors(state)
    doc_ids = {a.actor_id for a in actors.values() if a.kind == "INTERNAL_DOC"}
    leaf_rows = {l.leaf_id: l.row for l in leaves}
    hidden_rows = {leaf_rows[l.leaf_id] for l in _links(state)
                   if l.actor_id in doc_ids and leaf_rows.get(l.leaf_id)}
    hits = apply_rules(load_rules(), leaves, hidden_rows, classifications)
    return {"ok": True,
            "message": f"{len(classifications)} classified; cascade fired {len(hits)}×",
            "delta": {"classifications": classifications,
                      "cascade": [asdict(h) for h in hits]}}


# ── 5. decide (LLM batched) + per-leaf-ID reconciliation ─────────────────────

async def decide_stage(state, llm):
    leaves = _leaves(state)
    actors = _actors(state)
    links = _links(state)
    cascade = state.get("cascade", [])

    links_by_leaf: dict[str, list] = {}
    for l in links:
        links_by_leaf.setdefault(l.leaf_id, []).append(l)
    batch_ids = sorted(set(links_by_leaf) | {c["leaf_id"] for c in cascade})
    if not batch_ids:
        return {"ok": True, "message": "nothing linked — no decide call needed",
                "delta": {"decisions": []}}

    ph = {a.actor_id: a.placeholder for a in actors.values()}
    payload = {
        "task": "decide",
        "dictionary": {a.placeholder: {"name": a.name, "roles": a.roles}
                       for a in actors.values()},
        "leaves": [{
            "id": lid,
            "text": next(l.text for l in leaves if l.leaf_id == lid),
            "mentions": [{"surface": x.surface, "placeholder": ph[x.actor_id]}
                         for x in links_by_leaf.get(lid, [])],
            "cascade": [c for c in cascade if c["leaf_id"] == lid],
        } for lid in batch_ids],
    }
    data = llm.json_call(build_decide_prompt(payload), payload=payload)
    response = data.get("decisions", {})
    allowed = set(ph.values()) | {c["placeholder"] for c in cascade}

    def retry(leaf_id):
        p = {"task": "retry_leaf",
             "leaf": next(l for l in payload["leaves"] if l["id"] == leaf_id)}
        d = llm.json_call(build_retry_prompt(p), payload=p)
        return d.get("decisions", {}).get(leaf_id)

    decisions = reconcile_batch(batch_ids, response, allowed, retry_fn=retry)
    reviews = [d for d in decisions if d.decision == "REVIEW"]
    return {"ok": True,
            "message": f"{len(decisions)} decisions ({len(reviews)} REVIEW)",
            "delta": {"decisions": [asdict(d) for d in decisions]}}


# ── 6. validate + assemble (hard gates) ──────────────────────────────────────

async def assemble_stage(state, llm):
    leaves = _leaves(state)
    decided = {d["leaf_id"] for d in state.get("decisions", [])}
    decisions = [Decision(**d) for d in state.get("decisions", [])]
    decisions += [Decision(l.leaf_id, "KEEP", reason="no linked surfaces")
                  for l in leaves if l.leaf_id not in decided]
    res = validate_and_assemble(
        leaves, _links(state), decisions, _actors(state),
        [CascadeHit(**c) for c in state.get("cascade", [])])
    m = res.metrics
    return {"ok": True,
            "message": (f"DONE — coverage {m['coverage']:.2f}, "
                        f"{m['rewrites']} rewrites, {m['review']} REVIEW, "
                        f"{m['warnings']} warnings, silent losses {m['silent_losses']}"),
            "delta": {"payload": res.payload, "review_queue": res.review_queue,
                      "warnings": res.warnings, "metrics": m}}
