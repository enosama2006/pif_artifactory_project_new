"""REST surface — the agent's only public boundary (see docs/DESIGN_repo_and_ux.md §4).

POST /documents                 upload → {document_id}
POST /documents/{id}/runs       start pipeline → {run_id}
GET  /runs/{id}/events          SSE progress
GET  /runs/{id}/inventory       actors + placeholders
GET  /runs/{id}/decisions       leaf decisions (filter by status)
POST /runs/{id}/interventions   THE one write endpoint for every user action
POST /runs/{id}/rerun           targeted partial re-run
GET  /runs/{id}/payload         apply-payload keyed by leaf ID

Milestone 2 wires the pipeline behind these; the shapes are frozen now so the
add-in's typed client can be generated from /openapi.json immediately.
"""
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="anonymizer-agent", version="0.1.0")

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
    return {"ok": True}


@app.post("/documents")
def upload_document():
    raise NotImplementedError("Milestone 2")


@app.post("/runs/{run_id}/interventions")
def add_intervention(run_id: str, iv: Intervention):
    raise NotImplementedError("Milestone 4")
