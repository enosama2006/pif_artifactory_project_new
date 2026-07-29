# CLAUDE.md — project memory for new sessions

**What this is.** An institutional-document anonymizer: a Word Add-in
(`addin/`, plain Office.js) talks to a Python agent (`agent/`, Google ADK +
FastAPI) that replaces identity-bearing surfaces (people, org units, internal
doc names) with role-derived placeholders, consistently document-wide, with a
human-in-the-loop review flow. Clean successor to ten failed generations
(`docs/ANALYSIS_v1_to_v10.md`); the working language with the owner is
Arabic, but ALL code, UI and docs are English.

**Read before touching anything:**
- `docs/JOURNAL.md` — calibration-run history: every real-document run, what
  it exposed, what was fixed. THE file to reconstruct context.
- `docs/CHANGES.md` — change ledger (owner rule): every change-set says
  where it edited, why, and how to roll back just that piece. Append to it
  with every commit.
- `docs/BACKLOG.md` — agreed next steps + deliberately deferred questions.
- `docs/DESIGN_pipeline.md`, `docs/DESIGN_repo_and_ux.md` — architecture.
- `RUNBOOK.md` — how the owner runs everything (Windows .bat launchers).

**Iron rules (owner-agreed, learned the hard way):**
1. The LLM (Groq gpt-oss-120b) is NEVER responsible for coverage or
   consistency — deterministic code owns both; the LLM only makes narrow,
   validated judgments (closed enums, per-leaf-ID reconciliation).
2. Every piece of human-visible text is a leaf; sum of leaves across decide
   batches == tree total (enforced, not trusted). Structure (skeleton, table
   headers) rides along every batch as context, never as rewrite material.
3. A table row is an atomic block: never split across batches, applied as
   one unit in the add-in.
4. Placeholders come from the actor's generic FUNCTION, never echo the name;
   the dictionary is LOCKED before any rewriting.
5. Word apply is by content-control anchor (`anz:C_NNNNN`). The add-in
   AUTO-CLEANS stale anz tags before every run (owner rule: the document is
   clean before the agent receives it) and anchors fresh. Only leaves Word
   refuses to wrap (cover/title controls) fall back to a UNIQUE exact text
   match — ambiguity aborts. Every apply/locate outcome lands in the
   operation log.
6. All LLM calls run via asyncio.to_thread (blocking litellm would starve
   the event loop and freeze the progress API).
7. Every fix ships with a regression test keyed to the run that exposed it
   (`agent/tests/test_real_run_fixes.py`). Fix the block, never redesign the
   pipeline (the v1–v10 failure pattern).

**Calibration loop with the owner:** they run the add-in on a real PIF
policy doc, press "Copy diagnostics", paste the JSON here; analyze leaks/
quality, implement deterministic fixes + tests, push, they pull and rerun.

**State as of run 5 (a30d8030eb59) + fix package:** pipeline is
ingest → portrait (document context, ONE LLM call) → inventory →
surface_scan → classify_rules → decide → assemble. Placeholder minting is
function-aware (FUNCTION_TOKENS, frequency-ranked roles, PERSON-title rule);
add-in v0.7.0 auto-cleans anchors, logs every operation, and text-fallback
applies anchorless cover leaves. Run diagnostics carry events, per-stage
seconds, peak parallelism and the operation log. Remaining rough edges and
deferred items → `docs/BACKLOG.md`; rollback map → `docs/CHANGES.md`.
