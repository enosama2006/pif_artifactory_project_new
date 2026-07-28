# -*- coding: utf-8 -*-
"""Run the full pipeline on a document from the command line — no ADK, no server.

    cd agent
    python3 run_local.py path/to/document.docx

Uses Groq if GROQ_API_KEY is set (agent/.env is loaded automatically),
otherwise the no-key stub. Prints stage messages, final metrics, the payload
preview, and the REVIEW queue.
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _adk import stages
from app.llm import get_llm
from app.pipeline.validate_assemble import render_preview
from app.ingestion._contract import Leaf

STAGES = [stages.ingest_stage, stages.inventory_stage, stages.scan_stage,
          stages.classify_rules_stage, stages.decide_stage, stages.assemble_stage]


async def main(path: str) -> int:
    llm = get_llm()
    print(f"LLM mode: {'STUB (no GROQ_API_KEY)' if llm.is_stub else 'Groq'}")
    state: dict = {"input_path": path,
                   "_progress": lambda m: print(f"    … {m}")}
    for fn in STAGES:
        result = await fn(state, llm)
        print(f"[{fn.__name__:<22}] {result.get('message', '')}")
        state.update(result.get("delta", {}))
        if not result.get("ok", True):
            return 1

    leaves = {d["leaf_id"]: Leaf(**d) for d in state["leaves"]}
    print("\n=== PAYLOAD (apply by leaf ID) ===")
    for p in state.get("payload", []):
        before = leaves[p["leaf_id"]].text
        print(f"  {p['leaf_id']}  «{before[:50]}»")
        print(f"           → «{render_preview(leaves[p['leaf_id']], p['spans'])[:70]}»")
    print("\n=== REVIEW QUEUE ===")
    for r in state.get("review_queue", []):
        print(f"  {r['leaf_id']}  «{r['text'][:45]}» — {r['reason']}")
    print("\nMETRICS:", json.dumps(state.get("metrics", {}), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 run_local.py <document.docx | document.xml>")
    sys.exit(asyncio.run(main(sys.argv[1])))
