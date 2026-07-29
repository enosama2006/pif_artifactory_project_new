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
5. Word apply is by content-control anchor (`anz:C_NNNNN`), never text
   search; anchor numbering continues from the max existing tag.
6. All LLM calls run via asyncio.to_thread (blocking litellm would starve
   the event loop and freeze the progress API).
7. Every fix ships with a regression test keyed to the run that exposed it
   (`agent/tests/test_real_run_fixes.py`). Fix the block, never redesign the
   pipeline (the v1–v10 failure pattern).

**Calibration loop with the owner:** they run the add-in on a real PIF
policy doc, press "Copy diagnostics", paste the JSON here; analyze leaks/
quality, implement deterministic fixes + tests, push, they pull and rerun.

**State as of run 4 (72d2c2e3b84a):** tables extract (238 cells), 24 clean
actors, abbreviation table auto-feeds variants, missed-surface REVIEW channel
works, 384/384 decide coverage across 49 parallel batches. Remaining rough
edges and deferred items → `docs/BACKLOG.md`.
