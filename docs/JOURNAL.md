# Calibration Journal — real-document runs and what each one taught us

The project improves through a fixed loop: the owner runs the add-in on a
real PIF Data Governance Policy (.docx, EN, cover page + 6 tables + 60
numbered clauses), copies the diagnostics JSON into the chat, findings become
deterministic fixes + regression tests. This file is the durable record.

## Run 1 — `646d065f6ea4` (2026-07-28)
First real run. 160 leaves (all "paragraph"), 25 actors, 77 rewrites, 0 REVIEW.
**Found:** cover-page noise (48 "Click or tap…" leaves + aggregate duplicate);
5 leaves sharing one anchor would clobber the cover on apply; second mention
in digit-glued text ("2025Data Governance Policy") escaped the scan; "April
2025" missed by the date sweep; `<X> (“<X>”)` doubled definitions; missed
variants leaked silently (CoS Division, bare Digital & Technology,
Governance Hub); decide rubber-stamped 78/78 REWRITE.
**Fixed:** placeholder-text filter; aggregate dedupe; innermost-anchor map +
shared-anchor demotion; digit-boundary scan fix; Month-YYYY/ISO/Hijri date
patterns; definition collapse; residual leak sweep (acronym→REVIEW,
plain-token→warning); inventory prompt (variants/systems/structural-exclusion).

## Run 2 — `json_validate_failed` (2026-07-28)
Inventory died: Groq returned json_validate_failed 5× identically.
**Root causes:** reasoning model exhausted 4096 tokens before emitting JSON
(failed_generation empty); retries at temperature=0 replay the same failure;
custom heading styles → one giant section → one giant call.
**Fixed:** max_tokens 8192/16000; JSON-retry temperature escalation
(0→0.3→0.6→0.9→1.0); w:outlineLvl heading detection; 12k-char inventory
chunking; partial-chunk-failure resilience.

## Run 3 — `3e6163a5156e` (2026-07-28)
61 tiny chunks fragmented the inventory: 49 pseudo-actors, same committee got
`_2` placeholders (consistency broke), CoS DH hallucinated to "Department of
Health", phrase variants swallowed sentences ("PIF data systems" →
`<owner_organisation>` ate "data systems"), `<PIF_personnel>` leaked identity
inside a placeholder.
**Root cause:** numbered clauses carry w:outlineLvl → whole paragraphs became
headings → 60 one-sentence sections.
**Fixed:** outlineLvl counts as heading only ≤100 chars; identity merge
(article-prefix + shared-variant union, first-seen role wins); variant
trimming (wrapper phrases dropped); generic-pseudo-actor drop via shared
lexicon (`app/pipeline/_lexicon.py`); placeholder sanitization (identity
tokens stripped, clean charset). Also: event-loop starvation fix (all LLM
calls → asyncio.to_thread, parallel chunks, live "chunk 12/38" progress) and
structure/text separation (skeleton in every batch; plan_batches covers ALL
leaves with the sum==total invariant; decide 384/384).

## Run 4 — `72d2c2e3b84a` (2026-07-29)
Big success: tables extract (22 headers + 238 cells) after .iter-based
sdt-aware table parsing; 24 clean actors; abbreviation table auto-feeds
variants (D&T, DCGA, RAC, CR, CA, AIAA all linked); missed-surface REVIEW
fired for the first time ("Data Catalog"); full coverage 384/384 over 49
parallel batches.
**Found:** placeholder husks from name-echoing roles (`<Department>`,
`<Board_of>`, `<for_Data_and_Intelligence>`, `<actor>`, `<national>`);
"<X_department> Department" stutter after trimmed-variant replacement;
duplicate anchor tags across runs (anchorSeq restarted at 1 → false
shared-anchor REVIEWs); owner UX asks (Locate per actor, per-change Edit,
rows as one block).
**Fixed:** role scoring (function beats name-echo) + husk→kind fallback;
duplicate-tail-word collapse; anchor numbering continues from max existing
tag + duplicate-tag warning; payload carries row/column; add-in v0.6.0 —
Locate (cycles mentions), per-change Edit (saved as edit_leaf intervention),
table rows rendered/applied/rejected as one block.

## Post-run-4 UX fix (2026-07-29)
Owner report: Locate stopped finding the cover-page "Chief of Staff" title
(a built-in Word control box) after the Clean-anchors + re-anchor cycle.
**Cause:** Locate was anchor-tag-only; Clean anchors removed the old cover
tag, and on re-anchoring Word refuses to wrap paragraphs inside built-in
cover/title controls — those leaves end up with no anchor at all.
**Fix (ui v0.6.1):** Locate/goTo now try the anchor first and fall back to a
plain Word text search on the mention's surface (with occurrence cycling) —
safe because Locate only selects, never replaces. Floating text boxes
(shapes) remain unreachable by both methods; noted in BACKLOG.

## Run 5 — `a30d8030eb59` (2026-07-29)
Anchors clean (C_00001..C_00383 sequential), 384/384 coverage, 32 actors,
193 rewrites, 7 REVIEW — but placeholder quality REGRESSED and the add-in
hid failures.
**Found:** (1) PIF → `<data_training_participants>`: the role score's
`-len()` term preferred the LONGEST identity-free role; (2) seven units
collapsed into `<organisational_unit>.._7`: function vocabulary (technology,
cybersecurity, legal…) counted as identity, so every descriptive role
stripped to a husk; (3) CDO/PDPO/Legal Advisor → `<person>`/_2/_3: a job
title restated as role was treated as a name echo; (4) 4 false "invented
placeholder" REVIEWs: `use` came back comma-joined ("<a>, <b>") with every
tag in the dictionary; (5) "Chief Data Officer" missed-surface REVIEW: only
"Chief Data Officer (CDO)" and "CDO" were variants; (6) latent: LLM attached
the generic phrase "Advanced Analytics & AI" to D&T's variants — shared
generic variants can merge two REAL departments (order-dependent), and
"Records & …" vs "Records and …" made two actors; (7) add-in: apply
operations failed SILENTLY, cover leaves have no anchor and were skipped
with only a log line; stale anchors needed a manual Clean click; (8) owner
observed decide batches looking sequential; no way to verify from
diagnostics; (9) no portrait: Groq rewrote text without knowing what the
document IS.
**Fixed:** role ranking (2-word target + extraction frequency, longest-wins
removed); FUNCTION_TOKENS lexicon excluded from identity; PERSON-title rule
(`<chief_data_officer>`); ranked-roles minting with SDAIA >3-name-word
reconstruction guard; "&"→"and" key fold; parenthetical variant split;
identity-gated variant merge + polluted-variant drop; comma-joined `use`
accepted as advisory; NEW portrait stage feeding inventory, minting and
decide; peak-parallelism counters + timestamped run events + per-stage
seconds; add-in v0.7.0 — operation log (clean/anchor/locate/apply outcomes
in diagnostics), auto-clean anchors before every run, unique-text apply
fallback for anchorless leaves, applied/failed summaries.
**Replay proof:** run-5 actors through the new merge → 30 clean actors,
zero kind-fallback placeholders (`<owner_organization>`,
`<technology_department>`, `<cybersecurity_department>`,
`<chief_data_officer>`, …), AIAA restored as its own actor, Records
duplicates merged.

## Post-run-5 owner feedback (2026-07-29, before run 6)
Owner tested v0.7.0 partially and reported with screenshots:
(1) Locate could not reach the cover "Chief of Staff" — both cover cards
jumped to the same first hit; the cover text lives inside a BUILT-IN
DROPDOWN CONTROL ("Policy Owner Division"), which refuses anchoring and
constrains search/replace. Owner rule: unwrap such boxes and restore plain
text BEFORE the agent receives the document. (2) The changes list must
follow the PAPER ORDER, and REVIEW items must appear inline, editable, with
the original text standing — never auto-applied while REVIEW. (3) "April
2025" (the document's own date) was left as-is; ALL dates must be treated
per their context.
**Deep finding while fixing (3):** cascade fired 0× in run 5 — the rules
only triggered on hidden DOCUMENT names, so approval/tracking rows that hid
a person/org kept their dates and reference numbers; "92/1445" and
"Y24M06D02" were never even swept as candidates; and the YAML placeholders
were ARABIC (`<رقم_القرار>`) — they would have been injected into an
English document (iron-rule violation).
**Fixed (agent):** class-aware hidden rows + `identity_orphaned_qualifiers`
rule (hidden PERSON/ORG → same-row dates/refs follow); English cascade
placeholders (`<date>`, `<reference_number>`, `<contact_details>`);
reference-code sweep patterns; `document_date_on_cover` rule (a leaf that
IS a date, before the first heading → `<document_date>`); cascade is a RULE
— the LLM's KEEP cannot veto it (REVIEW still can); multiple cascade hits
per leaf now combine with mention spans.
**Fixed (add-in v0.7.1):** auto-clean now also UNWRAPS every content
control (text kept) so cover paragraphs become anchorable and findable;
changes list rendered in document order with REVIEW cards inline (badge +
reason, Edit/Locate only, excluded from Apply All until edited);
occurrence-exact text fallback (replace hit #n of m, abort on mismatch)
for both Locate and apply.

## Run 6 — `caf22833be79` (2026-07-29)
The fix packages held: portrait produced a correct document understanding
(owner, function, 12 key actors) and visibly improved placeholders
(`<enterprise_sponsor>`, `<technology_department>`, `<data_leadership_role>`);
cascade fired 13× (all approval/tracking dates and reference numbers masked,
cover `<document_date>` ×2); only 1 REVIEW (legit: missed 'BR'); peak
parallelism 6 proven by the event log; apply/locate logging worked exactly
as designed (it reported "0 matches" honestly).
**Found:** (1) cover leaves duplicated (two " April 2025" cards): the cover
text box exists twice in OOXML — mc:Choice + mc:Fallback of the same
mc:AlternateContent, and the parser walked both; (2) the cover date lives in
a FLOATING TEXT BOX — Word's own navigation shows "Textbox: April 2025" —
unreachable by body.search, hence locate/apply failed with 0 matches;
(3) actor fragmentation is order-dependent: Board/DASC/Digital & Technology/
Cybersecurity each split into _2/_3 actors because a late variant union
never re-checks collisions across actors, "(Board)" parentheticals broke
name keys, and the run-5 identity gate blocked all-function shared variants
('Digital & Technology', 'D&T'); (4) `<of_authority>` husk — identity
stripping left leading glue; (5) owner asked for a way to audit the parser's
extraction without spending a run.
**Fixed:** parser skips mc:Fallback subtrees (one copy per text box);
add-in v0.7.2 reaches floating text boxes via the Shapes API as a last
resort for BOTH locate and apply (logged); `_key` strips trailing
parentheticals; final `_consolidate` pass merges actors sharing an
identity-bearing or ≥2-word variant key regardless of insertion order;
deterministic abbreviation-table pairs ("D&T | Digital & Technology") link
actors the LLM split across chunks; compact ALL-CAPS acronyms ('D&T') count
as identity; the actor's own full name survives variant trimming (no more
"<governing_board> of Directors"); placeholders never start/end with glue
('delegation' added to FUNCTION_TOKENS → `<delegation_of_authority>`);
NEW `POST /parse` + "🔍 Parse check" button — ingest-only dry run listing
kinds, duplicate texts and full leaves, zero LLM cost.
**Replay proof:** run-6 actors through the new merge → 29 actors down to 24,
zero _2/_3 duplicates, full names in variants.

## How to continue
Next diagnostics paste → compare against Run 5: dictionary should look like
the replay above (no `<organisational_unit_N>`, no `<person_N>`); REVIEW
should drop to ~3 legitimate items (SDAIA directives — SDAIA was never
extracted; portrait may fix that too); `operation_log` in the diagnostics
now shows every apply outcome — check FAILED lines first; `events` +
"peak parallelism" in stage messages settle the sequential-batches question.
Then work `docs/BACKLOG.md` top-down. Every change-set is mapped in
`docs/CHANGES.md` for rollback.
