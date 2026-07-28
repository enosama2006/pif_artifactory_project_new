# Anonymizer — institutional document anonymization

Turn institutional Word documents into organisation-neutral, reusable versions:
every identity-bearing surface (people, org names, internal document references)
is replaced by a **role-derived placeholder**, consistently across the whole
document, with a human-in-the-loop convergence flow.

Clean successor to the `abstraction_agent` v1–v10 lineage. The full genealogy,
failure analysis, and design rationale live in [`docs/`](docs/).

## Layout — two fully independent components

```
agent/    Python service: ingestion blocks → 9-stage pipeline → REST API
addin/    Office.js Word Add-in: anchoring, upload, review UX
```

The only coupling is the OpenAPI schema served by the agent; the add-in
generates its typed client from it. No shared source, no shared build.

## The one-paragraph architecture

Deterministic code owns **coverage** and **consistency**; the LLM only makes
narrow, validated judgments. Parse produces a flat leaf inventory with stable
IDs (the coverage invariant). A per-section LLM pass builds an **actor
inventory**; a deterministic merge locks **one placeholder per actor role**
before any rewriting. A deterministic surface scan finds every mention.
Meaning-breakage cascades are **declarative rules**, not prompt prose. The
batched decide stage is reconciled **by leaf ID** — a dropped answer becomes a
visible REVIEW, never a silent loss. Application in Word is by content-control
anchor, never by text search.

## Status

Bootstrap. The pipeline mechanics are implemented and tested against
adversarial LLM stubs (`agent/tests/`); real LLM wiring and the add-in UI are
the next milestones (see `docs/DESIGN_repo_and_ux.md` §6 for build order).

## Development

```bash
cd agent
python3 -m pytest tests/ -v          # no external services needed
```
