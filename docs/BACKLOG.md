# Backlog — agreed next steps and deferred questions

Ordered by owner priority. Every item lands with a regression test.

## Now / next runs
1. **Run 6 calibration** — verify: dictionary matches the run-5 replay in
   `docs/JOURNAL.md` (no `<organisational_unit_N>`, no `<person_N>`);
   `operation_log` shows applies OK (check FAILED lines first — cover
   leaves should now apply via unique text match); portrait present in
   diagnostics and sane; "peak parallelism" ≥ 2 in inventory/decide stage
   messages; false "invented placeholder" REVIEWs gone (~3 legit REVIEWs
   expected: SDAIA directives — SDAIA was never in the inventory; check
   whether the portrait fixes that).
2. **Comment box + Redo (owner idea, 2026-07-29 — agreed design).**
   One unified comment box in the pane; a comment is either FREE (general
   guidance) or BOUND to a dictionary actor / proposed change. One "Redo"
   button processes accumulated comments:
   - BOUND path (build FIRST): a Groq ARBITER call with a CLOSED output —
     one of the existing intervention operations (rename_placeholder,
     merge_actors, add_surface, correct_role, ignore_actor, edit_leaf) —
     applied to the DB, then a PARTIAL re-run: deterministic re-scan +
     decide only for leaves touching the edited actors. Invalid arbiter
     output → shown for review, never executed (invented-placeholder
     philosophy). Builds on: stored interventions, the closed intervention
     enum in routes.py, and the partial-re-run backlog item below.
   - FREE path (second layer): a refine call turns comments into a USER
     GUIDANCE section injected into portrait/inventory/decide prompts
     (same pattern as the portrait), with the previous run's dictionary
     passed as a BINDING SEED so consistency survives the full re-run.
   - Every comment is stored as a `comment` intervention → per-organisation
     guidance memory reusable in future documents.
   - POST /runs body becomes JSON {ooxml, comments, seed_dictionary}
     (back-compat: raw OOXML body still accepted).
3. **Abbreviation/definition tables (DEFERRED by owner, needs a decision).**
   Rows like `PIF | Public Investment Fund` map BOTH cells to one actor →
   `<owner> | <owner>` (meaningless duplication). Options discussed:
   (a) deterministic rule: same actor in both cells of a Terms/Definition
   row → single placeholder cell + blank definition, or drop the row;
   (b) tell the decide LLM it is an abbreviation-expansion row and let it
   emit one merged rewrite; (c) leave to REVIEW. Assistant's lean: (a) —
   deterministic, no prompt reliance. Owner said: postpone, focus elsewhere.
4. **Org dictionary reuse** — seed inventory from previously confirmed
   actors of the same organisation (SQLite `actors` table already persists;
   wire a `document_id → org` grouping + seed payload).
5. **Golden corpus** — `agent/tests/golden/` still empty; the calibration
   doc + expected actors list should become the first entry so run-quality
   is CI-measurable instead of chat-reviewed.

## Known rough edges (small)
- `PIV` typo in source doc created a phantom ORG_OWNER actor — candidate for
  a fuzzy-merge (edit distance 1 to an existing acronym variant) or Ignore.
- Sweep warnings still slightly noisy («Authority», «Saudi», «Directors») —
  acceptable as warnings; revisit lexicon if they annoy.
- Page-header/footer leaves have no anchors (Office.js limitation) — manual
  apply; possible fix via header-specific OOXML rewrite export.
- Floating text boxes (Word shapes) are invisible to both getByTag and
  body.search; reaching them needs the WordApi 1.7+ shapes API.
- Decide reasons are terse; if REVIEW quality matters more later, ask for a
  one-line justification only on REVIEW.

## Larger, later
- Partial re-run after dictionary edits (stages 3+4 only, affected leaves) —
  interventions are recorded; the rerun endpoint exists as a stub concept in
  the design docs but is not implemented.
- Semantic re-identification sweep (quasi-identifiers) — RISKS.md #3.
- Arabic-document calibration run (all machinery is Arabic-aware but only
  tested synthetically).
- Export clean copy (accept-all + strip anchors) as one button.
- New ingestion formats (PDF) via the USD registry.
