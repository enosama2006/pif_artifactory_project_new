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


# ── 2. portrait (LLM, ONE call) — document context for every later stage ───
#
# Owner's design: before extraction, understand WHAT the document is — its
# function, owner, audience, and key actors' functions. Inventory extracts
# with that context, placeholder minting prefers the portrait's view of an
# actor's function, and decide rewrites knowing the document's purpose.
# A failed or stub portrait is EMPTY, never fatal: the pipeline degrades to
# context-free behavior instead of dying on one optional call.

async def portrait_stage(state, llm):
    import asyncio

    from app import config
    from app.pipeline.batching import build_skeleton
    from app.pipeline.portrait import build_portrait_prompt

    leaves = _leaves(state)
    sample, size = [], 0
    for lf in leaves:
        sample.append({"kind": lf.kind, "text": lf.text})
        size += len(lf.text)
        if size > config.PORTRAIT_SAMPLE_CHARS:
            break
    payload = {"task": "portrait", "skeleton": build_skeleton(leaves),
               "sample": sample}
    portrait: dict = {}
    try:
        data = await asyncio.to_thread(
            llm.json_call, build_portrait_prompt(payload), payload=payload)
        if isinstance(data, dict) and isinstance(data.get("portrait"), dict):
            portrait = data["portrait"]
    except Exception:
        portrait = {}
    if not portrait:
        return {"ok": True,
                "message": ("no portrait (stub mode or call failed) — "
                            "continuing without document context"),
                "delta": {"portrait": {}}}
    return {"ok": True,
            "message": (f"{str(portrait.get('document_function', '?'))[:90]} — "
                        f"owner: {portrait.get('owner', '?')}, "
                        f"{len(portrait.get('actors') or [])} key actor(s)"),
            "delta": {"portrait": portrait}}


# ── 3. inventory (LLM per section) + deterministic merge, dictionary LOCKED ─

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
    # LLM calls are BLOCKING (litellm + retry sleeps): they must run in
    # threads and in parallel, or they starve the event loop — the server
    # then cannot even answer the add-in's progress polls (real-run finding:
    # pane frozen on "starting the run", zero GET /runs/{id} in the log).
    import asyncio

    from app import config
    from app.pipeline.batching import build_skeleton

    progress = state.get("_progress") or (lambda msg: None)
    leaves = _leaves(state)
    portrait = state.get("portrait") or {}
    skeleton = build_skeleton(leaves)  # structure context in every chunk
    chunks = list(_inventory_chunks(leaves))
    sem = asyncio.Semaphore(config.MAX_CONCURRENCY)
    done, inflight, peak = 0, 0, 0

    async def run_chunk(sec, chunk):
        nonlocal done, inflight, peak
        payload = {"task": "inventory", "section": sec, "skeleton": skeleton,
                   "portrait": portrait,
                   "leaves": [{"id": l.leaf_id, "kind": l.kind, "text": l.text}
                              for l in chunk]}
        async with sem:
            inflight += 1
            peak = max(peak, inflight)
            progress(f"inventory: {done}/{len(chunks)} done, {inflight} in flight")
            try:
                data = await asyncio.to_thread(
                    llm.json_call, build_inventory_prompt(payload),
                    payload=payload, max_tokens=config.INVENTORY_MAX_TOKENS)
                return [a for a in data.get("actors", []) if isinstance(a, dict)]
            except Exception:   # one chunk must not kill the run — the residual
                return None     # leak sweep + review queue compensate downstream
            finally:
                inflight -= 1
                done += 1
                progress(f"inventory: {done}/{len(chunks)} done, {inflight} in flight")

    results = await asyncio.gather(*(run_chunk(s, c) for s, c in chunks))
    extractions = [r for r in results if r is not None]
    failed = sum(1 for r in results if r is None)
    if failed == len(chunks) and chunks:
        return {"ok": False,
                "message": f"inventory failed on all {failed} chunk(s) — aborting"}

    actors = merge_actors(extractions, portrait=portrait)
    msg = (f"{len(actors)} actors from {len(chunks) - failed}/{len(chunks)} chunk(s)"
           f" (peak parallelism {peak})"
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


# ── 4. surface scan (deterministic — the no-drop net) ───────────────────────

async def scan_stage(state, llm):
    links = scan(_leaves(state), _actors(state))
    return {"ok": True, "message": f"{len(links)} surface links",
            "delta": {"links": [asdict(l) for l in links]}}


# ── 5. candidates + closed-enum classify + breakage cascade ─────────────────

async def classify_rules_stage(state, llm):
    leaves = _leaves(state)
    candidates = sweep(leaves)
    classifications = []
    if candidates:
        import asyncio
        payload = {"task": "classify", "items": candidates}
        try:
            items = await asyncio.to_thread(   # blocking LLM call off the loop
                closed_enum_call, llm, build_classify_prompt(payload),
                enum=CLASS_ENUM, items_key="items", payload=payload)
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


# ── 6. decide (LLM, batched over ALL leaves) + per-leaf-ID reconciliation ───
#
# STRUCTURE vs TEXT separation (owner's design rule): the document skeleton
# accompanies EVERY batch as context; the text itself is partitioned so that
# every leaf lands in exactly one batch (invariant enforced by plan_batches).
# Unlinked leaves reach the LLM too — that is what makes the "missed surface
# → REVIEW" channel live (they previously never left the server).

async def decide_stage(state, llm):
    import asyncio

    from app import config
    from app.pipeline.batching import build_skeleton, plan_batches
    from app.pipeline.batching.plan import build_table_headers

    leaves = _leaves(state)
    actors = _actors(state)
    links = _links(state)
    cascade = state.get("cascade", [])
    portrait = state.get("portrait") or {}

    links_by_leaf: dict[str, list] = {}
    for l in links:
        links_by_leaf.setdefault(l.leaf_id, []).append(l)
    ph = {a.actor_id: a.placeholder for a in actors.values()}
    allowed = set(ph.values()) | {c["placeholder"] for c in cascade}
    leaf_by_id = {l.leaf_id: l for l in leaves}

    skeleton = build_skeleton(leaves)
    table_headers = build_table_headers(leaves)
    batches = plan_batches(leaves)
    dictionary = {a.placeholder: {"name": a.name, "roles": a.roles}
                  for a in actors.values()}

    def leaf_payload(lid):
        lf = leaf_by_id[lid]
        out = {"id": lid, "section": lf.section, "kind": lf.kind, "text": lf.text,
               "mentions": [{"surface": x.surface, "placeholder": ph[x.actor_id]}
                            for x in links_by_leaf.get(lid, [])],
               "cascade": [c for c in cascade if c["leaf_id"] == lid]}
        if lf.row:  # a cell: row is the atomic record, column gives meaning
            out["row"] = lf.row
            out["column"] = lf.col
        return out

    sem = asyncio.Semaphore(config.MAX_CONCURRENCY)
    progress = state.get("_progress") or (lambda msg: None)
    done, inflight, peak = 0, 0, 0

    def process_batch(batch, payload):
        """Whole batch — call, reconcile, per-leaf retries — runs in ONE
        thread so no blocking LLM call ever touches the event loop."""
        data = llm.json_call(build_decide_prompt(payload), payload=payload)
        response = data.get("decisions", {}) if isinstance(data, dict) else {}

        def retry(leaf_id):
            p = {"task": "retry_leaf",
                 "leaf": next(l for l in payload["leaves"] if l["id"] == leaf_id)}
            d = llm.json_call(build_retry_prompt(p), payload=p)
            return d.get("decisions", {}).get(leaf_id)

        return reconcile_batch(batch.leaf_ids, response, allowed, retry_fn=retry)

    async def run_batch(batch):
        nonlocal done, inflight, peak
        batch_tables = {(leaf_by_id[i].row or ":").split("r")[0]
                        for i in batch.leaf_ids if leaf_by_id[i].row}
        payload = {"task": "decide", "skeleton": skeleton,
                   "portrait": portrait,
                   "batch_sections": batch.sections, "dictionary": dictionary,
                   "table_headers": {t: h for t, h in table_headers.items()
                                     if t in batch_tables},
                   "leaves": [leaf_payload(i) for i in batch.leaf_ids]}
        async with sem:
            inflight += 1
            peak = max(peak, inflight)   # proves batches truly overlap
            progress(f"decide: {done}/{len(batches)} done, {inflight} in flight")
            try:
                return await asyncio.to_thread(process_batch, batch, payload)
            finally:
                inflight -= 1
                done += 1
                progress(f"decide: {done}/{len(batches)} done, {inflight} in flight")

    results = await asyncio.gather(*(run_batch(b) for b in batches),
                                   return_exceptions=True)
    decisions = []
    for batch, res in zip(batches, results):
        if isinstance(res, Exception):  # whole batch dead → visible REVIEWs
            decisions += [Decision(i, "REVIEW",
                                   reason=f"batch {batch.batch_id} failed: {res}")
                          for i in batch.leaf_ids]
        else:
            decisions += res

    assert len(decisions) == len(leaves), "decide lost coverage"  # the invariant
    reviews = sum(1 for d in decisions if d.decision == "REVIEW")
    return {"ok": True,
            "message": (f"{len(decisions)}/{len(leaves)} decisions across "
                        f"{len(batches)} batch(es), peak parallelism {peak} "
                        f"({reviews} REVIEW)"),
            "delta": {"decisions": [asdict(d) for d in decisions],
                      "batch_count": len(batches)}}


# ── 7. validate + assemble (hard gates) ──────────────────────────────────────

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
