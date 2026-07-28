"""Root ADK agent.

Google ADK is the orchestration backbone (sessions, runner, `adk web`), but —
lesson of the v1–v10 lineage — NO pipeline step is an LlmAgent: an LlmAgent
per step costs two wasted model round-trips each. The whole pipeline is one
DeterministicChain (BaseAgent); every LLM call happens INSIDE a stage through
the provider-agnostic `app.llm` boundary (Groq when GROQ_API_KEY is set,
otherwise a no-key stub so `adk web` works out of the box).

Usage in `adk web`: send the path of a .docx / document.xml as the message
(or attach the file). The chain reports each stage as it completes.
"""
from _adk.chain import DeterministicChain
from _adk import stages

root_agent = DeterministicChain(
    name="anonymizer",
    description=(
        "Anonymizes institutional Word documents: builds an actor inventory, "
        "locks one placeholder per role, finds every mention deterministically, "
        "applies meaning-breakage cascade rules, and emits leaf-ID-anchored "
        "replacement decisions with a REVIEW queue for the human loop."
    ),
    steps=[
        ("ingest", stages.ingest_stage),
        ("inventory", stages.inventory_stage),
        ("surface_scan", stages.scan_stage),
        ("classify_rules", stages.classify_rules_stage),
        ("decide", stages.decide_stage),
        ("assemble", stages.assemble_stage),
    ],
)
