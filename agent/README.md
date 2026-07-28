# agent/ — the anonymization service

Nine-stage pipeline; Groq (or any LLM) touches exactly four stages, each with a
narrow, validated job. See `../docs/DESIGN_pipeline.md`.

```
app/
├── api/                REST surface (FastAPI) — the only public boundary
├── ingestion/          input-format blocks → UnifiedDocument (USD)
│   └── ooxml/          .docx / document.xml (incl. headers & footers)
├── pipeline/
│   ├── inventory/      stage 3b: deterministic actor merge + placeholder minting
│   ├── surface_scan/   stage 5: Arabic-aware mention finding (offset-mapped)
│   ├── rules/          stage 7: declarative meaning-breakage cascades (YAML)
│   ├── decide/         stage 8: per-leaf-ID reconciliation of LLM batches
│   └── validate_assemble/  stage 9: coverage gate + apply-payload
├── llm/                provider-agnostic client; enum-validated re-roll loop
└── store/              SQLite persistence (runs, leaves, actors, decisions, interventions)
tests/                  adversarial-stub end-to-end + unit tests (no network)
```

Run tests: `python3 -m pytest tests/ -v`
