# Change ledger — what was touched, why, and how to roll it back

Owner's rule (2026-07-29): every change-set is recorded here from memory's
point of view — if something breaks after an edit, this file says WHERE it
was edited, WHY, and how to undo just that piece. Newest first. History
before this file exists is narrated in `docs/JOURNAL.md` (runs 1–4).

Each change-set maps to ONE commit; find its hash with
`git log --oneline --grep="<title>"`. Rolling back a whole set is
`git revert <hash>`; rolling back one piece is the per-file note below.

---

## Change-set: HITL run-2 fixes — name-chunk placeholders, rename routing, visible redo outcome (2026-07-29)

Trigger: run `abcd1b02f497` — owner commented "wrong anonymising, you must
abstract" on the NDMO cell (its placeholder was `<data_management_office>`,
a literal chunk of the org's name) and then reported "nothing happened when
pressing Send & Redo" (the arbiter had routed the complaint to a
rewrite_leaf, which correctly warned "no visible change" — but the warning
lived only in the monospace log).

### agent/app/pipeline/inventory/merge.py
- **What:** the name-restatement guard in `_mint_placeholder` now also
  rejects a non-PERSON role whose ≥3 substantive words form a CONTIGUOUS
  chunk of the actor's name/variant word sequence ("data management
  office" ⊂ "National Data Management Office"); scattered 3-word
  descriptions stay allowed ("data governance department" for the DCGA)
  and the old >3-scattered rule is unchanged.
- **Rollback:** restore the single `len > 3` condition; name-chunk
  placeholders return.

### agent/app/pipeline/arbiter/prompt.py
- **What:** decision-guide bullet: complaints about the REPLACEMENT itself
  ("wrong anonymising", "must be more abstract") → rename_placeholder for
  the actor of the leaf's linked mention, proposing an abstract tag.

### agent/app/pipeline/redo/engine.py
- **What:** `linked_mentions` in the arbiter's target context now carries
  `actor_id` + current `placeholder` per mention (a rename needs the id).

### addin/taskpane.js (0.8.1 → 0.8.2) + taskpane.html
- **What:** `renderRedoReport` — the last redo's outcome renders as
  colored cards in the Comments section (✓ / ⚠ warning / ✗ error, with
  mentions_linked); when zero cards changed it scrolls itself into view so
  the WHY is unmissable; cleared on a fresh run.
- **Rollback:** remove the div + function + two call sites; the outcome
  falls back to the log only.

### Tests
- 2 new: contiguous name-chunk never minted; the arbiter context carries
  actor_id/placeholder and a rename propagates end-to-end (82 total).

---

## Change-set: HITL run-1 fixes — popover UX, guided rewrites, honest reports (2026-07-29)

Trigger: run `996b0afbd924`, the first real use of the comment loop. Owner
findings: (1) 💬 jumped to the bottom instead of opening in place; (2) three
of four comments produced ZERO change while the log said "✓". Root causes:
rewrite_leaf was structurally toothless (the payload is built from spans
only, so the guided decide could obey the comment in its REASON while the
text never changed), and the arbiter picked rewrite_leaf where add_surface
was the only effective op ("CoS DH", the missed document name).

### agent/app/pipeline/arbiter/prompt.py
- **What:** "Decision guide" section: rewrite_leaf CANNOT anonymize
  anything new — a missed name/acronym/title is add_surface with the EXACT
  substring + the matching existing actor_id; rewrite_leaf is for wording
  problems on already-linked leaves; TARGET CONTEXT's linked_mentions list
  is the test for "missed".
- **Rollback:** remove the guide block; the misrouting returns.

### agent/app/pipeline/redo/engine.py
- **What:** (1) target context now carries `linked_mentions` of the bound
  leaf; (2) guided rewrites: a decide response for a user-guided leaf may
  carry full corrected text which OVERRIDES the span-rendered after
  (leak-gated: an acronymish identity token in it → REVIEW instead);
  (3) per-comment EFFECT in the report — rewrite/edit ops whose leaf did
  not change get `warning: "no visible change…"`, add_surface gets
  `mentions_linked` (link-count delta for the actor).
- **Rollback:** each is a separate block (7a, step 9, target_context).

### agent/app/pipeline/decide/reconcile.py + decide/prompt.py
- **What:** `Decision.rewrite` field — accepted ONLY when every tag inside
  is in the locked dictionary; the guidance prompt section documents the
  "rewrite" option (initial-run prompt unchanged — the section only exists
  when guidance does).
- **Rollback:** drop the field + the reconcile block; guided rewrites
  silently stop applying (the no-change warning will say so).

### addin/taskpane.js (0.8.0 → 0.8.1) + taskpane.html
- **What:** 💬 opens an in-place POPOVER on the card/row/actor (textarea +
  "🔁 Send & Redo" + "➕ Queue") — no jump to the bottom; the bottom
  section remains the drawer (pending list, selection comments, free
  comments); redo log lines now show mentions_linked and ⚠ no-change
  warnings.
- **Rollback:** restore bindToActor/bindToLeaf to the chip flow of 0.8.0.

### Tests
- 4 new: guided rewrite applied + reported, invented placeholder in a
  rewrite ignored, no-effect rewrite gets a visible warning, add_surface
  reports mentions_linked (80 total, green).

---

## Change-set: HITL comments + Redo, phase 1 — bound arbiter path (2026-07-29)

Trigger: owner-confirmed focus after run 7 — Human-in-the-loop that
ENRICHES the run. Architecture: `docs/DESIGN_hitl_comments.md` (read it
first; this entry is the rollback map).

### agent/app/pipeline/arbiter/ (NEW package)
- **What:** `ops.py` — the CLOSED operation set (add_surface,
  rename_placeholder, merge_actors, correct_role, ignore_actor, edit_leaf,
  rewrite_leaf, comment) + `validate_op` (unknown op/actor/leaf/format →
  user-facing error string, never executed); `prompt.py` — one comment in,
  one operation out.
- **Rollback:** the package is only imported by the redo engine — deleting
  both removes the feature cleanly.

### agent/app/pipeline/redo/ (NEW package)
- **What:** `engine.py` — `resolve_bind` (comment → leaf: explicit id →
  anchor → normalized paragraph text → unique selection containment;
  ambiguity reported, never guessed); `apply_op` (dictionary mutations,
  pure code); `redo_run` (arbiter per comment → validated ops → re-scan ALL
  leaves → cascade recompute from STORED classifications → decide
  mini-batch ONLY for leaves whose mention spans changed or rewrite_leaf
  targets, user guidance attached → merged decisions → full gates →
  edit_leaf overrides → updated_leaf_ids diff + redo_report).
- **Rollback:** same as arbiter — self-contained package.

### agent/app/pipeline/rules/engine.py
- **What:** NEW `compute_cascade(leaves, actors, links, classifications)` —
  the full deterministic cascade pass (class-aware hidden rows + rules +
  cover document date) extracted from `classify_rules_stage` so the initial
  run and the redo compute identical cascades.
- **Rollback:** inline it back into `_adk/stages.py` (pure move, no
  behavior change — the run-5/6 cascade tests prove it).

### agent/_adk/stages.py
- **What:** `classify_rules_stage` now calls `compute_cascade` (deleted the
  inlined block).

### agent/app/pipeline/decide/prompt.py
- **What:** optional `user_guidance` payload section ("BINDING — a human
  reviewed these leaves"); absent → the prompt is byte-identical to before,
  so the initial run is untouched.
- **Rollback:** remove `_guidance_section` and its call.

### agent/app/pipeline/inventory/merge.py
- **What:** NEW `mint_user_actor(name, kind, role, surfaces)` — a
  human-added actor bypasses the `_is_generic` pollution gate (the gate
  guards LLM extractions; the user's word is authoritative — "Strategy
  Office" is all-generic yet real) while reusing `_mint_placeholder`.
- **Rollback:** delete the function; redo's add_surface/new_actor path
  breaks (its test says so).

### agent/app/api/routes.py (0.4.0 → 0.5.0)
- **What:** `POST /runs/{id}/comments` (deterministic bind resolution at
  submit time + durable `comment` intervention row),
  `DELETE /runs/{id}/comments/{cid}`, `POST /runs/{id}/redo` (consumes all
  pending comments, replaces the run result in place, appends [redo] events,
  returns redo_report + updated_leaf_ids + full result); run records carry
  `comments`/`processed_comments`; INTERVENTION_TYPES += comment,
  rewrite_leaf.
- **Rollback:** the three endpoints and the two record keys are additive.

### addin/taskpane.js (0.7.2 → 0.8.0) + taskpane.html
- **What:** Comments section — one comment box, bind chip, 💬 buttons on
  dictionary rows and change cards, "💬 Comment on selection" (reads
  selection + paragraph + anchor tag from Word), pending drawer with
  ✕ remove, one "🔁 Redo with comments (N)" button, "↻ updated by your
  comment" badges from updated_leaf_ids, redo_report in the operation log,
  diagnostics v3 (+pending/processed comments, redo_report,
  updated_leaf_ids).
- **Rollback:** the section + the functions between the HITL banner comment
  and the diagnostics banner in taskpane.js; the 💬 buttons and
  `updatedBadge` in renderResults.

### Tests
- NEW `tests/test_hitl_comments.py` — 10 tests: closed-op gate (2), bind
  resolution never guesses, the owner's 3 cases end-to-end (missed surface
  finds siblings + re-decides only them; rename propagates with ZERO decide
  calls; rewrite_leaf carries guidance into the prompt), invalid arbiter
  output reported-not-executed, edit_leaf verbatim override, ignore_actor
  dissolves rewrites, comments API round-trip (76 total, green).

---

## Change-set: Run-7 fix package (2026-07-29)

Trigger: run `65f9eecf0ee2` + owner audit of "dropped" table cells (none
were silent — three distinct visible-but-wrong causes) + parse check
finding a doubled title leaf.

### agent/app/pipeline/inventory/merge.py
- **What:** (1) after consolidation, every variant is expanded to BOTH
  "&" and "and" notations; (2) `_drop_polluted_variants` keeps an
  all-generic variant that spells out the actor's acronym (new helper
  `_spells_acronym` — capitalized initials contain the name's letters in
  order).
- **Why:** L_000109 "Records & Administration Center Department" fell to
  REVIEW: the variant said "and", the doc wrote "&", and the expansion
  variant was being dropped as pollution because all its tokens are
  generic.
- **Rollback:** each is a self-contained block — remove the expansion loop
  (after `_consolidate`) and/or the `_spells_acronym` clause; the REVIEW
  returns.

### agent/app/pipeline/validate_assemble/validate.py
- **What:** KEEP branch — a leaf carrying locked-dictionary mention(s)
  now lands in `review_queue` ("model KEPT a leaf carrying dictionary
  mention(s): «…» — edit to apply the replacement") instead of a warning.
- **Why:** L_000017 "Board of Directors (Board)": Groq's KEEP was a silent
  veto; the owner saw the surface "vanish".
- **Rollback:** delete the `kept_links` block at the top of the KEEP
  branch; the old `_leaks_in` warning path underneath is unchanged.

### agent/app/pipeline/decide/reconcile.py
- **What:** `"use": ""` (blank/whitespace) is normalized to None before
  the locked-dictionary check.
- **Why:** L_000018 became a false "invented placeholder" REVIEW.
- **Rollback:** remove the two-line normalization.

### agent/app/ingestion/ooxml/block.py
- **What:** `_p_text` now excludes w:t under `mc:Fallback` AND under any
  NESTED `w:p` (the outer walk emits nested paragraphs as their own
  leaves).
- **Why:** an inline text box (mc:AlternateContent inside the paragraph)
  doubled L_000001 to "Data Governance Policy Data Governance Policy";
  the first fix attempt (Fallback-only) broke the run-6 nested-paragraph
  case by emitting the text at both levels.
- **Rollback:** restore the single-set version; the doubled title returns
  in one of the two text-box shapes depending on which line you keep.

### Tests
- 4 new run-7 tests: ampersand notation drift links both forms, blank
  `use` not invented, KEEP-with-links becomes visible REVIEW, inline
  fallback not doubled (66 total, all green).

---

## Change-set: Run-6 fix package (2026-07-29)

Trigger: run `caf22833be79` — duplicated cover leaves, cover date in a
floating text box, actor fragmentation (_2/_3), `<of_authority>` husk,
owner ask for a parser audit path.

### agent/app/ingestion/ooxml/block.py
- **What:** skip every element inside `mc:Fallback` — mc:AlternateContent
  carries the same text box twice (modern + legacy copies).
- **Why:** two " April 2025" leaves (L_000004/L_000007), doubled cover.
- **Rollback:** remove the `fallback_els` set + the skip; duplicates return.

### agent/app/pipeline/inventory/merge.py
- **What:** (1) `_key` strips a trailing parenthetical ("Board of Directors
  (Board)" collides with "Board of Directors"); (2) `_has_identity` counts
  compact ALL-CAPS acronyms ('D&T'); (3) NEW `_consolidate` pass after
  cleaning: actors sharing a consolidation key (identity-bearing or ≥2
  substantive words — never one generic word) merge regardless of insertion
  order; (4) NEW `abbreviation_pairs(leaves)` — deterministic (acronym,
  expansion) pairs from 2-cell acronym rows (≥2 capitals, expansion ≤60
  chars), applied in consolidation; (5) the actor's own name survives
  `_trim_wrapping_variants` (full "Board of Directors" replaced whole);
  (6) mint never leaves glue at tag edges.
- **Why:** run 6 shipped `<governing_board_2>`, `<steering_committee_2>`,
  `<technology_department_3>`, `<security_department_2>`,
  '<governing_board> of Directors' partial rewrites, `<of_authority>`.
- **Rollback:** each is a separate function/block; removing `_consolidate`
  restores order-dependent fragmentation only.

### agent/app/pipeline/_lexicon.py
- **What:** += delegation/delegations to FUNCTION_TOKENS.

### agent/_adk/stages.py (inventory)
- **What:** feeds `abbreviation_pairs(leaves)` into `merge_actors`.

### agent/app/api/routes.py
- **What:** NEW `POST /parse` — ingest-only dry run returning leaf list +
  kind counts (no LLM, temp file removed).
- **Why:** owner: "is the problem in the document or in the extraction?"

### addin/taskpane.js (0.7.1 → 0.7.2) + taskpane.html
- **What:** (1) `withShapeMatch` — floating-text-box fallback for BOTH
  locate and apply via the Shapes API (exact-text match, nth occurrence,
  tracked changes; every attempt logged, unsupported builds fail loudly);
  (2) search needles trimmed (leading-space " April 2025" broke matching);
  (3) "🔍 Parse check" button → POST /parse, logs kind counts + duplicated
  texts, dumps full leaves into the diagnostics box.
- **Rollback:** withShapeMatch is additive (last resort in the fallback
  chain); the button is standalone.

### Tests
- 4 new run-6 tests (fallback dedupe, late-variant consolidation,
  abbreviation-pair linking, glue-edged placeholder) + /parse endpoint
  test (62 total).

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
