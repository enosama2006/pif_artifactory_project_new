"""REST surface — the agent's only public boundary for the Word Add-in.

  GET  /health                        liveness + LLM mode + model
  POST /runs                          start a run (async); ?wait=1 runs inline
  GET  /runs/{run_id}                 live status: stage progress, then result
  POST /runs/{run_id}/interventions   record a user action (durable row)
  POST /runs/{run_id}/comments        add a pending HITL comment (bound/free)
  DELETE /runs/{run_id}/comments/{id} remove a pending comment
  POST /runs/{run_id}/redo            arbiter + partial re-run over the
                                      pending comments (docs/DESIGN_hitl_comments.md)

POST /runs body = the document itself: .docx bytes, raw word/document.xml,
or the Office.js getOoxml() package. The add-in polls GET /runs/{id} to show
live stage progress. CORS is wide open for development.

Run: uvicorn app.api.routes:app --port 8080   (from the agent/ directory)
"""
import asyncio
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # agent/ on sys.path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="anonymizer-agent", version="0.5.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

INTERVENTION_TYPES = {
    "rename_placeholder", "merge_actors", "correct_role", "add_surface",
    "ignore_actor", "accept_leaf", "reject_leaf", "edit_leaf", "annotate",
    "comment", "rewrite_leaf",
}

STAGE_NAMES = ["ingest", "portrait", "inventory", "surface_scan",
               "classify_rules", "decide", "assemble"]

RUNS: dict[str, dict] = {}   # in-memory run registry (results also hit SQLite)


class Intervention(BaseModel):
    type: str
    target: str          # actor_id or leaf_id
    payload: dict = {}
    note: str = ""


class Comment(BaseModel):
    text: str
    # bind: {leaf_id?|actor_id?|anchor?, paragraph_text?, selected_text?}
    bind: dict = {}


@app.get("/health")
def health():
    from .. import config
    from ..llm import get_llm
    return {"ok": True,
            "llm_mode": "stub" if get_llm().is_stub else "groq",
            "model": config.LLM_MODEL,
            "version": app.version}


async def _execute(run_id: str, doc_path: str) -> None:
    import time

    from _adk import stages as S
    from ..llm import get_llm
    from ..store import Store

    rec = RUNS[run_id]
    llm = get_llm()
    rec["llm_mode"] = "stub" if llm.is_stub else "groq"
    # Live sub-stage detail ("decide: 12/49 done, 6 in flight") for the
    # add-in's poll, PLUS a timestamped event history — the owner must be
    # able to see afterwards what happened in the background and when
    # (run-5 ask: prove whether batches truly overlap).
    t0 = time.monotonic()
    rec["events"] = []

    def _progress(msg: str) -> None:
        rec["detail"] = msg
        rec["events"].append({"t": round(time.monotonic() - t0, 1), "msg": msg})
        if len(rec["events"]) > 1000:          # bounded — this record is polled
            del rec["events"][:200]

    state: dict = {"input_path": doc_path, "_progress": _progress}
    fns = [S.ingest_stage, S.portrait_stage, S.inventory_stage, S.scan_stage,
           S.classify_rules_stage, S.decide_stage, S.assemble_stage]

    for name, fn in zip(STAGE_NAMES, fns):
        rec["current_stage"] = name
        rec["detail"] = ""
        _progress(f"stage {name} started")
        stage_t0 = time.monotonic()
        try:
            result = await fn(state, llm)
        except Exception as exc:  # surface, never hang
            rec.update(status="error", error=f"{name}: {exc!r}", current_stage=None)
            return
        rec["stages"].append({"stage": name,
                              "message": result.get("message", ""),
                              "ok": result.get("ok", True),
                              "seconds": round(time.monotonic() - stage_t0, 1)})
        _progress(f"stage {name} finished in {round(time.monotonic() - stage_t0, 1)}s")
        state.update(result.get("delta", {}))
        if not result.get("ok", True):
            rec.update(status="error", error=result.get("message"), current_stage=None)
            return

    Path(doc_path).unlink(missing_ok=True)
    rec["result"] = {
        "metrics": state.get("metrics", {}),
        "actors": state.get("actors", {}),
        "payload": state.get("payload", []),
        "review_queue": state.get("review_queue", []),
        "warnings": state.get("warnings", []),
        # diagnostics: original text per leaf + scan links + raw batch
        # decisions, so a run can be evaluated end-to-end from one response
        "leaves": state.get("leaves", []),
        "links": state.get("links", []),
        "decisions": state.get("decisions", []),
        "classifications": state.get("classifications", []),
        "cascade": state.get("cascade", []),
        "portrait": state.get("portrait", {}),
    }
    rec.update(status="completed", current_stage=None)
    try:
        Store().save_run(run_id, document_id=run_id, status="completed")
    except Exception:  # persistence is best-effort; the response is the product
        pass


@app.post("/parse")
async def parse_only(request: Request):
    """Ingest-only dry run: returns the parser's leaves so extraction can be
    audited against the document BEFORE spending any LLM call (owner ask,
    run 6: 'is the problem in the document or in the extraction?')."""
    raw = await request.body()
    if not raw:
        return {"ok": False, "error": "empty body — send the document bytes"}
    tmp = tempfile.NamedTemporaryFile(prefix="anz_parse_", delete=False)
    tmp.write(raw)
    tmp.close()
    from _adk import stages as S
    state: dict = {"input_path": tmp.name}
    try:
        result = await S.ingest_stage(state, None)
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    if not result.get("ok", True):
        return {"ok": False, "error": result.get("message")}
    leaves = result["delta"]["leaves"]
    kinds: dict[str, int] = {}
    for lf in leaves:
        kinds[lf["kind"]] = kinds.get(lf["kind"], 0) + 1
    return {"ok": True, "message": result.get("message", ""),
            "leaf_count": result["delta"]["leaf_count"],
            "kinds": kinds, "leaves": leaves}


@app.post("/runs")
async def create_run(request: Request, wait: int = 0):
    raw = await request.body()
    if not raw:
        return {"ok": False, "error": "empty body — send the document bytes"}
    tmp = tempfile.NamedTemporaryFile(prefix="anz_api_", delete=False)
    tmp.write(raw)
    tmp.close()

    run_id = uuid.uuid4().hex[:12]
    RUNS[run_id] = {"run_id": run_id, "status": "running",
                    "current_stage": None, "stages": [], "result": None,
                    "comments": [], "processed_comments": []}
    if wait:
        await _execute(run_id, tmp.name)
        return {"ok": RUNS[run_id]["status"] == "completed", **RUNS[run_id]}
    asyncio.get_running_loop().create_task(_execute(run_id, tmp.name))
    return {"ok": True, "run_id": run_id, "status": "running",
            "stage_names": STAGE_NAMES}


@app.get("/runs/{run_id}")
def get_run(run_id: str):
    rec = RUNS.get(run_id)
    if rec is None:
        return {"ok": False, "error": "unknown run_id"}
    return {"ok": rec["status"] != "error", **rec}


@app.post("/runs/{run_id}/interventions")
def add_intervention(run_id: str, iv: Intervention):
    from ..store import Store
    if iv.type not in INTERVENTION_TYPES:
        return {"ok": False, "error": f"unknown type {iv.type!r}",
                "allowed": sorted(INTERVENTION_TYPES)}
    Store().add_intervention(run_id, iv.type, iv.target, iv.payload, iv.note)
    return {"ok": True}


# ── HITL comments + Redo (docs/DESIGN_hitl_comments.md) ─────────────────────

@app.post("/runs/{run_id}/comments")
def add_comment(run_id: str, c: Comment):
    """Accumulate a pending comment. The binding is resolved DETERMINISTICALLY
    now (anchor → paragraph text → selection), so the pane can show the user
    which leaf their comment landed on before anything runs."""
    from app.ingestion._contract import Leaf
    from ..pipeline.redo import resolve_bind
    from ..store import Store

    rec = RUNS.get(run_id)
    if rec is None:
        return {"ok": False, "error": "unknown run_id"}
    if rec["status"] != "completed" or not rec.get("result"):
        return {"ok": False, "error": "run not completed — comments attach to results"}
    if not c.text.strip():
        return {"ok": False, "error": "empty comment"}

    leaves = [Leaf(**d) for d in rec["result"].get("leaves", [])]
    resolved = resolve_bind(c.bind, leaves)
    comment = {"id": uuid.uuid4().hex[:8], "text": c.text.strip(),
               "bind": c.bind, "resolved": resolved}
    rec.setdefault("comments", []).append(comment)
    try:   # durable: every comment is org memory even before the redo
        Store().add_intervention(
            run_id, "comment",
            resolved.get("leaf_id") or resolved.get("actor_id") or "unbound",
            {"bind": c.bind, "resolved": resolved}, c.text.strip())
    except Exception:
        pass
    return {"ok": True, "comment": comment,
            "pending": len(rec["comments"])}


@app.delete("/runs/{run_id}/comments/{comment_id}")
def remove_comment(run_id: str, comment_id: str):
    rec = RUNS.get(run_id)
    if rec is None:
        return {"ok": False, "error": "unknown run_id"}
    before = len(rec.get("comments", []))
    rec["comments"] = [c for c in rec.get("comments", []) if c["id"] != comment_id]
    return {"ok": len(rec["comments"]) < before,
            "pending": len(rec["comments"])}


@app.post("/runs/{run_id}/redo")
async def redo(run_id: str):
    """Process ALL pending comments: one closed-output arbiter call each,
    validated ops applied to the dictionary, then the deterministic partial
    re-run (re-scan → cascade → decide only affected leaves → gates).
    Invalid arbiter output lands in redo_report — shown, never executed."""
    import time

    from ..llm import get_llm
    from ..pipeline.redo import redo_run

    rec = RUNS.get(run_id)
    if rec is None:
        return {"ok": False, "error": "unknown run_id"}
    if rec["status"] != "completed" or not rec.get("result"):
        return {"ok": False, "error": "run not completed"}
    comments = rec.get("comments", [])
    if not comments:
        return {"ok": False, "error": "no pending comments"}

    t0 = time.monotonic()

    def _progress(msg):
        rec["detail"] = msg
        rec.setdefault("events", []).append(
            {"t": round(time.monotonic() - t0, 1), "msg": f"[redo] {msg}"})

    try:
        delta = await redo_run(rec["result"], comments, get_llm(),
                               progress=_progress)
    except Exception as exc:
        return {"ok": False, "error": f"redo failed: {exc!r}"}

    report = delta.pop("redo_report")
    updated = delta.pop("updated_leaf_ids")
    rec["result"].update(delta)
    rec["result"]["redo_report"] = report
    rec["result"]["updated_leaf_ids"] = updated
    rec.setdefault("processed_comments", []).extend(comments)
    rec["comments"] = []
    _progress(f"redo done: {len(report)} comment(s), "
              f"{len(updated)} leaf/leaves updated")
    return {"ok": True, "redo_report": report, "updated_leaf_ids": updated,
            "metrics": rec["result"].get("metrics", {}),
            "result": rec["result"]}
