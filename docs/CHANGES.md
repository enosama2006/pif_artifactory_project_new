# Change ledger — what was touched, why, and how to roll it back

Owner's rule (2026-07-29): every change-set is recorded here from memory's
point of view — if something breaks after an edit, this file says WHERE it
was edited, WHY, and how to undo just that piece. Newest first. History
before this file exists is narrated in `docs/JOURNAL.md` (runs 1–4).

Each change-set maps to ONE commit; find its hash with
`git log --oneline --grep="<title>"`. Rolling back a whole set is
`git revert <hash>`; rolling back one piece is the per-file note below.

---

## Change-set: Post-run-5 owner feedback (2026-07-29)

Trigger: owner screenshots — cover "Chief of Staff" is a built-in dropdown
control Locate can't reach; list must follow paper order with editable
inline REVIEW; "April 2025" (document date) left unchanged; dates per
context. Deep finding: cascade fired 0× in run 5 and YAML placeholders
were Arabic.

### agent/app/pipeline/rules/breakage_rules.yaml
- **What:** placeholders now ENGLISH (`<date>`, `<reference_number>`,
  `<contact_details>`); `when_hidden_class` is a list; new rule
  `identity_orphaned_qualifiers` (hidden PERSON/ORG_* → same-row
  dates/reference numbers follow).
- **Why:** run 5: only hidden DOC names triggered rules → cascade 0×;
  Arabic tags violated the English-only iron rule.
- **Rollback:** restore previous YAML; engine still accepts single-string
  `when_hidden_class`.

### agent/app/pipeline/rules/engine.py
- **What:** `hidden_rows` is now `{row: {hidden classes}}` (plain set still
  accepted = class unknown); per-surface dedupe across rules; fallback
  placeholder `<redacted>`.
- **Rollback:** revert this function only; the YAML list-form
  `when_hidden_class` also needs reverting then.

### agent/app/pipeline/candidates/sweep.py
- **What:** `_DATE_PATTERNS` extracted + `whole_text_is_date()`; new
  DECISION_NO patterns for `92/1445` (lookarounds keep it out of full
  dates) and compact codes like `Y24M06D02`.
- **Rollback:** drop the two patterns + helper; classify/cascade for those
  codes disappears again.

### agent/_adk/stages.py (classify_rules_stage)
- **What:** hidden rows collected from ALL actor links with kind→class
  mapping; deterministic `document_date_on_cover` pass (whole-leaf date,
  no row, before the first heading → `<document_date>`).
- **Rollback:** restore the doc-only `hidden_rows` set and delete the
  cover-date loop.

### agent/app/pipeline/validate_assemble/validate.py
- **What:** cascade hits are a RULE — a KEEP decision on a cascade leaf is
  upgraded to REWRITE (REVIEW still wins); multiple cascade hits per leaf;
  cascade spans merge with mention spans (overlap defers to the mention).
- **Why:** Groq had KEPT the dates; single-hit dict silently dropped extras.
- **Rollback:** restore the single `cascade_by_leaf` dict block — the KEEP
  override is the piece that forces date masking, remove it consciously.

### addin/taskpane.js (0.7.0 → 0.7.1)
- **What:** (1) `autoCleanAnchors` now unwraps EVERY content control (text
  kept, `cannotDelete` cleared, one-by-one retry fallback) — cover dropdown
  boxes become plain, anchorable text; (2) changes list rendered in
  document order (payload sorted by leaf id; row card sits at its first
  cell); (3) REVIEW leaves absorbed into the list as inline cards (badge +
  reason, Edit/Locate only, `after = before` until edited, skipped by
  Apply All); (4) occurrence-exact text fallback `replaceByTextOccurrence`
  (hit #n of m, abort on count mismatch) + `anchorlessOrdinal` used by
  goTo and applyOne.
- **Rollback:** each function is separately revertable from the previous
  commit; unwrapping is the behavioral change to reconsider first if a
  document's controls must survive.

### Tests
- 5 new: identity-row cascade, English-placeholder guard, reference-code
  sweep, cover document date, cascade-overrides-KEEP (57 total).
- Updated: end-to-end cascade expectations to English placeholders.

---

## Change-set: Run-5 fix package (2026-07-29)

Trigger: run `a30d8030eb59` diagnostics + owner findings (silent apply
failures, "stupid" placeholders, no portrait stage, batches looking
sequential, manual anchor cleaning).

### agent/app/pipeline/_lexicon.py
- **What:** added `FUNCTION_TOKENS` (technology, cybersecurity, legal,
  authority, director, organisation, …).
- **Why:** these words describe what an actor DOES; counting them as
  identity stripped every descriptive role to a husk → the
  `<organisational_unit>.._7` flood.
- **Rollback:** delete the set and its two imports (merge.py); the flood
  returns but nothing else breaks.

### agent/app/pipeline/inventory/merge.py  (largest edit — placeholder quality)
- **What:** (1) role ranking rewritten (`_rank_roles`): survives-stripping →
  fewest identity echoes → closest to 2 substantive words → highest
  extraction frequency → first seen. The old `-len()` term preferred the
  LONGEST role (PIF → "data training participants"). (2) `_mint_placeholder`
  now walks ranked roles and takes the first that yields a clean tag; the
  kind fallback is last resort only. A non-PERSON role restating >3 words of
  the actor's own name is rejected (SDAIA guard); a PERSON title restated is
  ACCEPTED (`<chief_data_officer>`, not `<person>`). (3) `_key` folds "&" to
  "and" (Records & / and duplicate). (4) parenthetical variants split:
  "Chief Data Officer (CDO)" also yields "Chief Data Officer" and "CDO"
  (run-5 missed surface). (5) variant-based actor merge now requires an
  IDENTITY-bearing shared variant, and `_drop_polluted_variants` removes
  all-generic variants unrelated to the actor's name (run-5 latent bug:
  "Advanced Analytics & AI" pollution on D&T). (6) `merge_actors` accepts
  `portrait=` role hints (weight 3).
- **Why:** owner: "بليس هولدر غبي تمامًا" — PIF→`<data_training_participants>`,
  7×`<organisational_unit_N>`, `<person>`/_2/_3 for titled persons.
- **Rollback:** `git show <prev>:agent/app/pipeline/inventory/merge.py` —
  the file is self-contained; only `stages.py` passes the new `portrait=`
  kwarg (remove it too if reverting).

### agent/app/pipeline/decide/reconcile.py
- **What:** a REWRITE whose `use` is a comma-joined list of placeholders
  that are ALL in the locked dictionary is accepted with `placeholder=None`
  (spans drive the rewrite) instead of REVIEW.
- **Why:** 4 false "invented placeholder" REVIEWs on rows like "DCGA, D&T".
- **Rollback:** remove the `_TAG.findall` branch; false REVIEWs return.

### agent/app/pipeline/portrait/ (NEW) + prompts + wiring
- **What:** new portrait stage — ONE LLM call after ingest describing the
  document (summary, function, owner, audience, key actors + functions).
  Wired: `agent.py` steps, `routes.py` STAGE_NAMES/fns/result, payload of
  every inventory chunk and decide batch (context section in both prompts),
  role hints into placeholder minting, `portrait` label in the add-in.
- **Why:** owner's design: Groq must know WHAT it is editing so replacement
  text and placeholders are informed by document context.
- **Rollback:** remove the step from `agent.py` + `routes.py` lists — the
  stage is additive; every consumer treats a missing/empty portrait as
  "no context" and works exactly as before.

### agent/_adk/stages.py
- **What:** portrait stage added; inventory + decide progress now report
  `X/Y done, N in flight` and the stage message reports **peak
  parallelism** (proves batches truly overlap — owner suspected sequential
  execution).
- **Why:** run-5 ask: richer background visibility.
- **Rollback:** the instrumentation is the `inflight/peak` counters only.

### agent/app/api/routes.py
- **What:** `events` — timestamped progress history per run (bounded 1000),
  `seconds` per stage, portrait in result, stage list + version 0.4.0.
- **Why:** diagnostics must show afterwards what happened in the background
  and when.
- **Rollback:** delete `_progress`'s events append and the `seconds` field.

### addin/taskpane.js (ui 0.6.1 → 0.7.0)
- **What:** (1) `OPLOG` operation log — every clean/anchor/locate/apply
  records its outcome, shipped in diagnostics as `operation_log`; (2)
  `autoCleanAnchors()` runs BEFORE anchoring on every run (owner rule:
  clean before the agent receives the document) — the manual button now
  shares this code; (3) `applyOne` logs success/failure per item and falls
  back to a **unique exact text match** when a leaf has no anchor (cover/
  title controls) — zero or multiple matches abort safely; (4) `applyAll`/
  `applyRow` print applied/failed summaries; (5) locate logs why the anchor
  path failed before falling back to text search; (6) diagnostics v2 adds
  ui_version, events, portrait, operation_log.
- **Why:** run-5: replacements failed SILENTLY; stale anchors needed a
  manual clean step.
- **Rollback:** each piece is a separate function; the pre-0.7.0 behavior
  of any of them is in the previous commit of this file.

### Tests (agent/tests/)
- `test_real_run_fixes.py`: 8 new run-5 tests (role ranking, title persons,
  function-word units, &-merge, parenthetical variants, comma-joined `use`,
  portrait hint, generic-variant pollution).
- `test_stages.py`: portrait stage in the scripted pipeline.
- `test_anchoring_api.py`: stage list includes portrait.
