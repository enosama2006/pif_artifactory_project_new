# agent/ — the anonymization agent (Google ADK)

Google ADK is the orchestration backbone (sessions, runner, `adk web`), applied
with the lineage's hardest-won lesson: **no pipeline step is an LlmAgent**.
The root agent is one `DeterministicChain` (a BaseAgent) running six stage
functions; every LLM call happens *inside* a stage through the provider-
agnostic `app.llm` boundary. See `../docs/DESIGN_pipeline.md`.

## Running

```bash
pip install -e ".[dev]"          # or: pip install google-adk litellm fastapi pyyaml python-dotenv pytest
cp .env.example .env             # put GROQ_API_KEY there (loaded automatically)

# A) ADK web UI — run from the REPO ROOT (adk web lists packages in cwd):
cd ..
adk web
# pick "agent", send the PATH of a .docx or document.xml as the message
# (or attach the file). Watch the six stages report as they complete.

# B) CLI, no server:
python3 run_local.py path/to/document.docx

# C) Tests (no network, no key — adversarial LLM stubs):
python3 -m pytest tests/ -v
```

No `GROQ_API_KEY` → the agent still runs end-to-end in **stub mode**
(0-actor inventory, mechanical classify/decide) so wiring can be verified
anywhere; with the key it makes the real Groq calls.

## Layout

```
agent.py                 root_agent (DeterministicChain — ADK entry point)
_adk/
├── chain.py             BaseAgent: runs stages, propagates state_delta, intake
└── stages.py            six pure async stage functions (testable without ADK)
app/
├── ingestion/           input blocks → UnifiedDocument (docx incl. page headers)
├── pipeline/
│   ├── inventory/       actor merge + LOCKED placeholder dictionary (+ prompts)
│   ├── candidates/      deterministic pattern sweep (dates, decision numbers…)
│   ├── surface_scan/    Arabic-aware offset-mapped mention finding
│   ├── rules/           breakage_rules.yaml + deterministic cascade engine
│   ├── decide/          per-leaf-ID reconciliation (+ prompt)
│   └── validate_assemble/  coverage gate + leaf-ID payload
├── llm/                 get_llm(): GroqAdapter (quirks quarantined) | StubLlm
├── store/               SQLite persistence
└── api/                 REST surface for the Word Add-in (Milestone 3+)
tests/                   12 tests: units + full docx→metrics runs
```

## Who does what (per stage)

| stage | LLM (Groq) | agent (deterministic) |
|---|---|---|
| ingest | — | docx/xml → leaf inventory `L_NNNNNN` (coverage invariant) |
| inventory | extract actors+roles per section | merge duplicates, mint & LOCK one placeholder per actor |
| surface_scan | — | find every mention (normalized, offset-mapped, overlap-free) |
| classify_rules | closed-enum classify of pattern candidates | candidate sweep; breakage cascades from YAML rules |
| decide | REWRITE/KEEP/REVIEW per leaf, dictionary-only | reconcile by leaf ID; missing → retry → auto-REVIEW; invented placeholder → REVIEW |
| assemble | — | coverage gate (must be 1.0), payload by leaf ID + offsets |
