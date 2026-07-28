"""REST surface — the agent's only public boundary for the Word Add-in.

  GET  /health                        liveness + LLM mode + model
  POST /runs                          start a run (async); ?wait=1 runs inline
  GET  /runs/{run_id}                 live status: stage progress, then result
  POST /runs/{run_id}/interventions   record a user action (durable row)

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

app = FastAPI(title="anonymizer-agent", version="0.3.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

INTERVENTION_TYPES = {
    "rename_placeholder", "merge_actors", "correct_role", "add_surface",
    "ignore_actor", "accept_leaf", "reject_leaf", "edit_leaf", "annotate",
}

STAGE_NAMES = ["ingest", "inventory", "surface_scan",
               "classify_rules", "decide", "assemble"]

RUNS: dict[str, dict] = {}   # in-memory run registry (results also hit SQLite)


class Intervention(BaseModel):
    type: str
    target: str          # actor_id or leaf_id
    payload: dict = {}
    note: str = ""


@app.get("/health")
def health():
    from .. import config
    from ..llm import get_llm
    return {"ok": True,
            "llm_mode": "stub" if get_llm().is_stub else "groq",
            "model": config.LLM_MODEL,
            "version": app.version}


async def _execute(run_id: str, doc_path: str) -> None:
    from _adk import stages as S
    from ..llm import get_llm
    from ..store import Store

    rec = RUNS[run_id]
    llm = get_llm()
    rec["llm_mode"] = "stub" if llm.is_stub else "groq"
    # live sub-stage detail ("inventory: chunk 12/38") for the add-in's poll
    state: dict = {"input_path": doc_path,
                   "_progress": lambda msg: rec.__setitem__("detail", msg)}
    fns = [S.ingest_stage, S.inventory_stage, S.scan_stage,
           S.classify_rules_stage, S.decide_stage, S.assemble_stage]

    for name, fn in zip(STAGE_NAMES, fns):
        rec["current_stage"] = name
        rec["detail"] = ""
        try:
            result = await fn(state, llm)
        except Exception as exc:  # surface, never hang
            rec.update(status="error", error=f"{name}: {exc!r}", current_stage=None)
            return
        rec["stages"].append({"stage": name,
                              "message": result.get("message", ""),
                              "ok": result.get("ok", True)})
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
    }
    rec.update(status="completed", current_stage=None)
    try:
        Store().save_run(run_id, document_id=run_id, status="completed")
    except Exception:  # persistence is best-effort; the response is the product
        pass


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
                    "current_stage": None, "stages": [], "result": None}
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
