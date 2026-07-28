"""REST surface — the agent's only public boundary for the Word Add-in.

MVP contract (synchronous — institutional documents run in seconds):

  GET  /health                        liveness + LLM mode
  POST /runs   (body = document)      run the full pipeline, return everything
  POST /runs/{run_id}/interventions   record a user action (durable row)

The body of POST /runs is the document itself in any accepted form:
.docx bytes, raw word/document.xml, or the Office.js getOoxml() package.
CORS is wide open for development; tighten before any shared deployment.

Run: uvicorn app.api.routes:app --port 8080   (from the agent/ directory)
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # agent/ on sys.path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI(title="anonymizer-agent", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

INTERVENTION_TYPES = {
    "rename_placeholder", "merge_actors", "correct_role", "add_surface",
    "ignore_actor", "accept_leaf", "reject_leaf", "edit_leaf", "annotate",
}


class Intervention(BaseModel):
    type: str
    target: str          # actor_id or leaf_id
    payload: dict = {}
    note: str = ""


@app.get("/health")
def health():
    from ..llm import get_llm
    return {"ok": True, "llm_mode": "stub" if get_llm().is_stub else "groq"}


@app.post("/runs")
async def create_run(request: Request):
    import tempfile

    from _adk import stages
    from ..llm import get_llm
    from ..store import Store

    raw = await request.body()
    if not raw:
        return {"ok": False, "error": "empty body — send the document bytes"}

    tmp = tempfile.NamedTemporaryFile(prefix="anz_api_", delete=False)
    tmp.write(raw)
    tmp.close()

    llm = get_llm()
    state: dict = {"input_path": tmp.name}
    stage_log = []
    for fn in (stages.ingest_stage, stages.inventory_stage, stages.scan_stage,
               stages.classify_rules_stage, stages.decide_stage, stages.assemble_stage):
        try:
            result = await fn(state, llm)
        except Exception as exc:  # surface, never 500 silently
            return {"ok": False, "error": f"{fn.__name__}: {exc!r}", "stages": stage_log}
        stage_log.append({"stage": fn.__name__, "message": result.get("message", "")})
        state.update(result.get("delta", {}))
        if not result.get("ok", True):
            return {"ok": False, "error": result.get("message"), "stages": stage_log}
    Path(tmp.name).unlink(missing_ok=True)

    run_id = uuid.uuid4().hex[:12]
    try:
        Store().save_run(run_id, document_id=run_id, status="completed")
    except Exception:  # persistence is best-effort; the response is the product
        pass

    return {
        "ok": True,
        "run_id": run_id,
        "llm_mode": "stub" if llm.is_stub else "groq",
        "stages": stage_log,
        "metrics": state.get("metrics", {}),
        "actors": state.get("actors", {}),
        "payload": state.get("payload", []),
        "review_queue": state.get("review_queue", []),
        "warnings": state.get("warnings", []),
    }


@app.post("/runs/{run_id}/interventions")
def add_intervention(run_id: str, iv: Intervention):
    from ..store import Store
    if iv.type not in INTERVENTION_TYPES:
        return {"ok": False, "error": f"unknown type {iv.type!r}",
                "allowed": sorted(INTERVENTION_TYPES)}
    Store().add_intervention(run_id, iv.type, iv.target, iv.payload, iv.note)
    return {"ok": True}
