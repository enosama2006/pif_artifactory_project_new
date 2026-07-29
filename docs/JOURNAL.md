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

## How to continue
Next diagnostics paste → compare against Run 4: placeholder quality
(`<IT_department>`-style expected), false shared-anchor REVIEWs should vanish
after Clean anchors + rerun, table rows should apply as blocks. Then work
`docs/BACKLOG.md` top-down.

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
