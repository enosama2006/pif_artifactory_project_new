# Next-Project Design — Light, Precise, No-Drop Abstraction Pipeline

Companion to `ANALYSIS_v1_to_v10.md`. This document answers three concrete failures
the owner observed across all ten generations, and specifies a minimal architecture
for the successor repo, including storage and the human-in-the-loop journey.

## 0. The three observed failures, diagnosed from the code

**(a) Headings treated as node headers, never sent as text.**
True in v1–v6: `extract_rewriteable_units` emitted only paragraph/list_item/table_row;
heading text reached the LLM only inside `path` metadata. Fixed twice — v7 (headings
become units) and again v10 (`ooxml_to_tree` makes heading text its own `TextLeaf`).
**Rule for the successor: every piece of human-visible text is a leaf. No exceptions —
headings, document title, meta lines, table headers, captions.**

**(b) Paragraphs "silently dropped" despite full batching.**
The text *does* reach Groq. What gets lost is the *decision on the way back*, via four
documented mechanisms:
1. Silence-equals-keep semantics (v1–v8): the LLM omits a unit, and the server treats
   omission as "keep" — invisible loss. v9 outlawed this with an explicit `keep`
   decision enforced by a pydantic validator.
2. Groq JSON-mode drift: from the second array element on, items come back as quoted
   strings instead of objects; defensive normalisation drops them.
3. Whole-unit string-equality law: a pair whose `original_text` differs by one
   character from the source is rejected (`not_substring_of_source`: 57 in one run).
4. Batch hard-timeout → fallback stubs.
**Rule: coverage is reconciled by ID, never by trust.** Every leaf ID sent must come
back with a decision; missing → single-leaf retry; still missing → auto-REVIEW
(v10 `decide_unit` already implements exactly this).

**(c) Placeholder inconsistency across batches** ("أحمد عبدالرحمن" → «مسؤول الاعتماد»
in batch 1, «مسؤول الإدارة» in batch 3; company name → «اسم الشركة» then «اسم المؤسسة»).
Root cause: naming was decided *inside* the batch call, where the model has no memory.
The lineage's answer (v4 → v10) is the **identity/mentions split**: identity is decided
once, globally, before any batch runs; batches only *apply* a fixed dictionary.
**Rule: the batch-stage LLM may not invent placeholders. It selects from a
pre-assigned dictionary; the validator rejects invented ones (v10 `validate_decisions`
already flags `invented-placeholder`).**

## 1. Governing principle

> The LLM is never responsible for coverage or consistency. Deterministic code owns
> both. The LLM only makes judgments on material that deterministic code hands it,
> and every judgment is validated by ID against what was sent.

## 2. The pipeline — six stages, three LLM calls-per-role

```
 stage                     executor        writes (DB table)
 1. parse                  Python          documents, leaves        (L_000001 …)
 2. inventory              LLM (per       section_analyses,
    (actors+roles)          section, ∥)    actors, actor_roles      → placeholder dict
 3. surface_scan           Python          surface_links            (leaf_id ↔ actor)
 4. decide                 LLM (batched)   leaf_decisions           (REWRITE/KEEP/REVIEW)
 5. validate + assemble    Python          warnings, payloads       (Add-in JSON by leaf ID)
 6. HITL converge          human + Python  interventions            → partial re-run of 3+4
```

Stage details:

1. **parse** — OOXML → immutable tree → flat leaf list. Stable IDs `L_NNNNNN` in
   document order; kind ∈ {title, meta, heading, paragraph, list_item, table_header_cell,
   table_cell, caption}. Deterministic; no LLM. A leaf-count invariant is asserted:
   `sum(kind counts) == leaves in payload` at every later stage.
2. **inventory** — per-section LLM calls (parallel, section-bounded so context is never
   torn mid-topic) extract *actors* (people, org units, the owner org, external bodies,
   systems) with their **documented role** and surface variants. A deterministic merge
   unifies duplicates across sections and mints **one placeholder per (actor, role)** —
   e.g. `<ROLE_APPROVAL_OFFICER>`, `<ISSUING_ORGANISATION>`. The role is the
   abstraction: the placeholder names the function, not the entity.
3. **surface_scan** — pure Python (word-bounded, quote/hyphen/Arabic-normalised,
   longest-surface-first) locates *every* mention of every surface variant in *every*
   leaf. This is the no-drop guarantee for known actors: even if the LLM later ignores
   a mention, the link exists and the validator will flag it.
4. **decide** — batched LLM (~4k chars, section-bounded, table rows atomic). Input per
   batch: leaves pre-tagged with their scan hits + the *relevant slice* of the
   placeholder dictionary. Output per leaf: REWRITE (with spans → placeholder chosen
   from the dictionary), KEEP, or REVIEW, plus any *newly noticed* candidate surface
   (goes to inventory as a REVIEW-status actor, not applied directly). Reconciliation:
   one decision per sent leaf ID; missing → single-leaf retry → auto-REVIEW.
5. **validate + assemble** — Python: coverage (every leaf decided, every surface link
   addressed or explicitly kept), consistency (no invented placeholders, same surface
   → same placeholder everywhere), then the Add-in payload **keyed by leaf ID + char
   offsets**, not by text search. Retire string-equality matching entirely.
6. **HITL converge** — see §4.

## 3. Storage & reuse

SQLite (or Postgres) via idempotent writers, keyed `(document_id, run_id)` — v10's
schema is the template: `runs, leaves, section_analyses, actors, actor_roles,
surface_links, leaf_decisions, human_interventions`. Consequences:

- Each stage writes immediately when it completes → a crash never loses upstream work;
  a re-run resumes from the last completed stage.
- The **actor inventory is a reusable asset across documents of the same
  organisation**: run 2 on a sibling document seeds stage 2 with the stored inventory,
  so placeholders stay consistent org-wide, not just document-wide.
- Every human intervention is a durable row → the same correction never has to be
  made twice.

## 4. The human-in-the-loop journey

The realistic end state of any automatic run is "mostly right + a REVIEW list" —
so the product is the *convergence loop*, not the first run. Concretely, in the
Word Add-in sidebar:

**Step 1 — review the inventory (the placeholder dictionary), not the document.**
A table: placeholder · role · #mentions · sample sentence. The human acts here with
five operations (all exist as v10 tools): rename placeholder, merge two actors,
correct a role, add a missed surface variant, ignore an actor (keep it visible).
This is the highest-leverage screen: one rename fixes 40 mentions at once.

**Step 2 — targeted partial re-run (the piece v10 never implemented).**
An intervention marks the affected actor(s); the engine re-runs **only stages 3–4,
only on leaves linked to those actors** (cheap: one scan + a handful of small
batches). Everything else is untouched. This is why stage outputs are stored per leaf.

**Step 3 — clear the REVIEW queue.**
Each REVIEW leaf is shown with its reason and a one-tap resolution:
apply-suggestion / keep / edit manually. Decisions are recorded as interventions.

**Step 4 — apply as tracked changes.**
The payload applies by leaf ID; Word's native accept/reject is the final,
familiar review surface. Rejections can be read back as interventions.

Loop 1→4 until the REVIEW queue is empty. Store everything; the next document from
the same org starts from step 1 with a pre-filled dictionary.

## 5. What deliberately stays out (lightness)

- No orchestrator/conversation LlmAgent routing — a plain HTTP API with explicit
  endpoints (`/run`, `/inventory`, `/intervene`, `/rerun`, `/payload`) is simpler and
  removes a whole class of ADK routing/transfer quirks. ADK remains optional glue,
  not the backbone.
- No witness/synthesizer portrait; the per-section inventory pass already reads the
  whole document. A short document profile can be a single cheap call if needed.
- No numeric self-confidence, no quality-gate enums (proven signal-free).
- One model boundary (`llm_client`) with swappable provider + structured-output
  support; Groq-specific repair stays as a quarantined adapter, not a design driver.

## 6. Taming a non-compliant model: classify → rules → check

Ten generations proved that a weak model's compliance cannot be bought with longer
prompts (v10's decide prompt reached 361 lines and still erred). The successor
shrinks the judgment surface per call instead:

1. **Classify (LLM, closed enum).** Per surface/cell, one multiple-choice question:
   `class ∈ {INTERNAL_DOC_NAME, INSTANCE_IDENTIFIER, QUALIFIER_OF_IDENTIFIER,
   PERSON, ORG_UNIT, EXTERNAL_REF, DOMAIN_TERM, …}`. Closed output space →
   near-perfect compliance, trivially validated, re-rolled on violation.
2. **Decide (Python rule engine).** The "meaning-breakage tests" become declarative
   cascade rules in `breakage_rules.yaml`, applied deterministically over the
   classifications — e.g. *if an INTERNAL_DOC_NAME in a table row is hidden, hide
   that row's INSTANCE_IDENTIFIER and QUALIFIER_OF_IDENTIFIER too (a decision number
   and date are worthless without their referent)*. Rules cannot be "forgotten" in
   one batch and applied in another; new patterns found during human review are added
   as rules + golden-corpus test cases, never as prompt prose.
3. **Check (LLM, yes/no).** After the rules compose the rewritten unit, a micro-call
   asks only "is this still coherent and readable?" — No → REVIEW.

Supporting tactics: table rows get their own micro-prompt (row + column headers,
never mixed with paragraphs); self-consistency voting (3 samples, majority,
disagreement → REVIEW) only for low-agreement leaves; no corpus-derived examples in
prompts (documented drift cause); and the `llm_client` boundary allows placing a
stronger model on a single low-volume stage if golden-corpus metrics prove voting
insufficient — decided by numbers, not impressions.

This is the same principle that fixed placeholder consistency (identity decided once
/ mentions found by Python), applied one level deeper: **classification by the model,
decision by rules, verification by a binary question.** v1's "mechanical case
derivation" failed because its tags were invented freely inside the rewrite batch
itself; here classification is closed, separate, and validated before rules touch it.

## 7. Definition of done (before writing code)

Golden corpus: 5–10 real documents (incl. TOC-bearing, long tables, Arabic).
Metrics ratcheted in CI on every PR:
- leaf coverage = decided/total (must be 1.0 by construction — regression test);
- surface recall vs a human-annotated mention list per corpus doc;
- placeholder consistency violations = 0;
- REVIEW ratio (drives prompt work);
- interventions-to-clean = how many human actions until the REVIEW queue is empty
  (the true product metric).
