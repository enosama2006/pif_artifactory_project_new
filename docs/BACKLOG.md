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
2. **Comment box + Redo — HITL that ENRICHES the run.
   ✅ PHASE 1 (bound path) IMPLEMENTED 2026-07-29 — architecture in
   `docs/DESIGN_hitl_comments.md`, rollback in `docs/CHANGES.md`, 10 tests.
   Remaining: phase 2 (free-comment full re-run with seed dictionary) and
   the org-memory seeding (item 4). Original agreed design below.**
   Purpose: every run occasionally drops an entity; the human closes the
   gap AND the correction becomes stored knowledge for future documents.

   The owner's three cases and how each maps onto the design:
   - **Case 1 — missed surface.** The user SELECTS the text in Word and
     writes a comment ("this is the X department, it was missed").
     UX: a "💬 Comment on selection" button in the pane reads
     `context.document.getSelection()` — the comment arrives already
     BOUND to an exact surface + its leaf, no typing the surface by hand.
     Arbiter output: `add_surface` (possibly + `merge_actors` /
     new-actor), then partial re-run picks up every sibling mention.
   - **Case 2 — dictionary edit must propagate.** 💬 on a dictionary row;
     the comment edits/renames/ignores the actor. Arbiter output:
     `rename_placeholder` / `correct_role` / `ignore_actor` /
     `merge_actors` applied to the DB, then partial re-run regenerates
     every LINKED leaf so texts and dictionary never diverge.
   - **Case 3 — unconvincing rewrite / table row.** 💬 on a change card
     ("I want this row to say …"). This is a DIFFERENT path from the
     initial run: the arbiter emits `edit_leaf` (direct replacement) or
     `rewrite_leaf` (NEW op — re-decide that leaf with the user's comment
     attached to the prompt as binding guidance).

   Flow (bound path, build FIRST):
   1. Comments accumulate in a pending drawer (counter badge); nothing
      fires until ONE "🔁 Redo with comments" click — batching is the UX
      contract, the user reviews in peace.
   2. Per comment, ONE Groq arbiter call with CLOSED output: an existing
      intervention op (add_surface, rename_placeholder, merge_actors,
      correct_role, ignore_actor, edit_leaf) + new `rewrite_leaf` +
      `comment` (no-op, memory only). Invalid/out-of-enum output is SHOWN,
      never executed (invented-placeholder philosophy).
   3. PARTIAL re-run, deterministic first: re-scan new/changed surfaces
      across ALL leaves (pure code, catches siblings); decide mini-batches
      ONLY for affected leaves (comment text attached); reconcile + gates
      as usual. Untouched leaves keep their results verbatim.
   4. UI diffs the result: cards touched by the redo get an "updated by
      your comment" badge; the user sees exactly what their comment did.
   5. Every comment is stored as a `comment` intervention keyed to the
      organisation → seed dictionary + guidance memory for future runs.

   FREE path (second layer): unbound comments go through a refine call →
   USER GUIDANCE section injected into portrait/inventory/decide prompts,
   with the previous run's dictionary as a BINDING SEED, full re-run.

   API: POST /runs/{id}/comments (accumulate) + POST /runs/{id}/redo;
   POST /runs body becomes JSON {ooxml, comments, seed_dictionary}
   (back-compat: raw OOXML body still accepted).

   Guardrail delivered already (run-7 fix): a Groq KEEP on a leaf carrying
   dictionary mentions is now a visible editable REVIEW card — "silently
   dropped" cells can no longer hide, which is what triggered this item.

   Deferred by owner (same message): further add-in search/replace
   constraint cleanup.
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
