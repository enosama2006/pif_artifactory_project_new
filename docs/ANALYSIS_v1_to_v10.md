# abstraction_agent v1 → v10 — Genealogy, Recurring Failures, and Lessons for the Next Project

**Purpose.** This document is the distilled understanding of all ten generations of the
abstraction agent in this repository (v1–v4, v6–v10; v5 is absent from this snapshot).
It exists to plan the successor project on GitHub without re-deriving — or worse,
re-losing — three months of lessons. Evidence for every claim lives in the version
folders themselves; git history is a single commit, so the code and its docstrings are
the only record.

**The mission (unchanged across all ten versions).** Take an institutional OOXML Word
document (policy, SOP, governance manual), and produce an organisation-neutral,
reusable "pattern" of it — concretely, a list of replacement decisions a Word Add-in
applies as tracked changes. "Maven for documents." All versions run on Google ADK +
LiteLLM + Groq `openai/gpt-oss-120b`, called from a Word Add-in via HTTP.

---

## 1. Genealogy — what each version was, and why it existed

| Ver | Date (approx) | One-line identity |
|-----|---------------|-------------------|
| v1 | May 2026 | Baseline 3-phase SequentialAgent: parse → portrait+DNA (3 witnesses + synthesizer) → per-batch replacements under the WHOLE-UNIT law |
| v2 | mid-May | Deliberate reset to 2 phases to iterate on a cheap single-call portrait; parked "knowledge mind pass-1" experiment (killed by Groq TPM limits) |
| v3 | 2026-05-14 | **Frozen checkpoint** of a successful experiment: 9-block document profile (1 call) + span-based replacements. Validated: 60 pairs on FALAH doc |
| v4 | late May | First **canonicals** layer: whole-doc LLM canonical extraction → deterministic Python linker → canonical-aware replacements → deterministic enrichment |
| v5 | — | Missing from this snapshot |
| v6 | early June | Hybrid consolidation: v1's witness portrait + v4's canonicals/linker + v1's six-layer rewrite prompt with per-batch canonical filtering |
| v7 | June | + Headings become rewriteable units (owner-bound section titles had been escaping abstraction) |
| v8 | June | Full text-coverage unit contract (9 unit kinds, single source of truth shared by linker and batcher), budget-only batching, per-run diagnostics + router-call logging |
| v9 | 2026-06-11 | Re-architecture: LlmAgent **orchestrator** + conversation sub-agent; decoupled tool blocks; **markdown handoff** (parse → markdown → project-owned UnifiedTree); DeterministicChain; SQLite persistence |
| v10 | 2026-06-15→18 | Re-conceptualization: tree-centric, **role-centered** 9-stage pipeline — portrait → parallel section analysis → actor inventory (multi-role placeholders) → deterministic surface scan → pre-tagging → batched REWRITE/KEEP/REVIEW decisions → validate → assemble; HITL intervention tools. Paused right after its first successful full-document runs |

### The three eras

1. **Monolith era (v1–v8).** One `SequentialAgent`, ~20 files, ~5k lines. Each generation
   is a copy-paste folder mutating prompts, the unit contract, and the phase list.
   Intelligence oscillates between "one big call" and "many small calls".
2. **Block era (v9).** The 2026-06-11 refactor: one folder per tool
   (`__init__.py` + `contract.py` + `tool.py`), a state-key registry, infrastructure /
   contracts / shared separation, an orchestrator routing documents → pipeline and
   everything else → conversation. Triggered concretely by a real document whose
   `<w:toc>` node crashed pydantic validation — the fix was to stop owning the
   parser's type vocabulary and hand off **markdown** between phases.
3. **Role era (v10).** The conceptual redesign: abstraction is not find-and-replace of
   entities; it is comprehension. Per-section analysis produces `line_essences` and
   actor mentions; a deterministic merge builds an **actor inventory** where one actor
   carries multiple roles and each role gets one placeholder; a deterministic surface
   scan guards against LLM omission; a final batched LLM stage decides
   REWRITE/KEEP/REVIEW per leaf.

### Ideas that were tried, abandoned, and (sometimes) resurrected

- **Whole-unit rewrite law** (v1) → spans-within-unit (v3) → hybrid `rewrite_mode` (v4)
  → whole-unit again (v6–v9) → per-leaf decisions with placeholders (v10).
- **applies_to local/global + Case A/B/C/D + GLOBAL ⊇ LOCAL** (v1) → dropped for a
  three-swap breakage test (v4) → restored (v6–v9) → replaced by five breakage tests
  keyed by entity class (v10).
- **Mechanical confidence/quality-gate machinery** (contract v3.1): removed after a real
  run showed `mean_confidence = 0.992` across 155/155 accepted pairs — the numbers
  carried no signal. Numeric self-grading by the LLM was judged decorative.
- **3 witnesses + synthesizer portrait** (v1, v6–v9) ↔ **single-call portrait**
  (v2, v3, v10). v10's final answer: single call with 60/20/20 head/middle/tail
  sampling above 35k chars.
- **Node.js parser subprocess** (v1–v8) → in-process Python parser (v9+), with the dead
  `parser_js/` folder still shipped.

### Ideas that survived every generation (the convergent core)

These are the hard-won invariants; the next project should treat them as settled:

1. **The LLM never sees raw XML.** Parse deterministically; give the model a readable
   rendering (markdown) with stable inline anchors (`[p_0001]`-style IDs).
2. **Identity vs. mentions split.** The LLM identifies *one* canonical/actor
   ("the issuing organisation"); deterministic Python finds the *many* mentions
   (word-bounded, quote/hyphen-normalised, longest-surface-first). "Python grep is
   more reliable for the 'many' side than the LLM is."
3. **The role IS the abstraction.** The replacement for "PIF" is not `<ORG_1>` but the
   documented role: `<ISSUING_ORGANISATION>`. This survived from v4's
   `role_in_document` to v10's actor-role placeholders.
4. **Describe, then decide.** A purely descriptive portrait/profile stage (explicitly
   forbidden from recommending treatment) precedes any abstraction decision.
5. **No silent skipping.** Every notable value must appear in an emitted decision or in
   an explicit considered-but-kept list; missing units get fallback stubs; every drop
   has a typed reason counted in a summary.
6. **Deterministic stages are free; LLM stages are suspect.** Batching, linking,
   validation, enrichment, assembly — all pure Python. Server-side validation rejects
   LLM output that violates the contract rather than trusting it.
7. **Section-aware batching** (~4–5k chars, table rows atomic, header context carried)
   with bounded concurrency, per-batch hard timeouts, and per-unit recovery.
8. **Every LLM call is dumped to disk** (prompt + response + meta) because one
   unreproducible `json_validate_failed` incident cost a debugging session.

---

## 2. The recurring failure modes (why ten versions did not finish)

### F1 — The model, not the architecture, was the ceiling

An enormous fraction of every version is defensive plumbing around one cheap model
(Groq `gpt-oss-120b`): `json_validate_failed` re-rolls, top-level-array coercion,
missing-wrapper tolerance, string-instead-of-object drift on repetitive batches,
field-name-swap repair (one session had **47/47 canonicals fail validation** before a
shape-repair function was written), `reasoning_content` rejection requiring a
`thought=True`-stripping callback on *every* LlmAgent, TPM rate-limit deaths, and a
older, backoff ladders tuned to it. The architecture was rewritten ten times;
the model was never once reconsidered.

### F2 — Text-equality anchoring is brittle

Matching replacements back by exact string equality (whole-unit / whole-cell law, or
verbatim span substrings) produced high rejection rates in real runs — e.g.
**75 of 284 pairs rejected** in one v6-era run (`not_substring_of_source`: 57).
The law exists because substring search in the Add-in causes off-target rewrites —
but the root cause is that the *application* side matches by text instead of by the
stable node IDs the parser already assigns.

### F3 — Coverage leaks discovered one generation at a time

Owner-bound text kept escaping: section headings (fixed v7), document title / meta
lines / table column headers (fixed v8), heading text lost in the markdown round-trip
(fixed v10 by making heading text its own leaf). Each leak cost a generation because
there was no coverage metric ratcheting on a fixed corpus.

### F4 — Prompt ontology churn

Every generation rewrote the conceptual vocabulary: portrait+DNA → 9-block profile →
canonicals (6 kinds) → actors/roles/glossary/essences. Prompts grew to 300+ lines of
layered thinking with named confusion modes. Some machinery demonstrably carried no
signal (F-note above on confidence). There was never an A/B harness to show a prompt
change helped; "reads well" was the acceptance test.

### F5 — No definition of done, no evaluation

Zero test suites with assertions across all ten versions (v10's `_e2e_test.py` stubs
all LLM stages and asserts nothing). No golden corpus. No metric. The final v10 run
*completed* — 355 leaves, 127 rewrites, coverage 0.862, but also 49 REVIEWs and
25 high-severity warnings with `needs_human_review=true` — and there was no defined
threshold that would have called that success or failure. The project didn't fail a
test; it had no test to fail.

### F6 — The human loop was designed but never closed

v10 built six HITL intervention tools (override decision, correct role, merge actors,
rename placeholder …) each logging to a `human_interventions` audit table — but the
partial re-run mechanism they all point to (`requires_rerun` hints) was never
implemented. The conversation agent can only *tell the user* a re-run is needed.
Since the realistic end state of any run is "mostly right + dozens of REVIEWs", the
missing human-convergence loop is the missing product.

### F7 — Process: copy-paste versioning, one commit, stale docs

Ten sibling folders, one git commit, READMEs copied byte-identical between versions
(v4's README describes v3), ARCHITECTURE.md drifted from code within one generation,
dead code shipped alongside its replacement (`parser_js/`, `runner.py`, unused
`plan_batches` on the critical path). Framework friction was rediscovered repeatedly
(ADK `transfer_to_agent` is one-shot; unhandled tool exceptions truncate SSE and hang
the SequentialAgent; LlmAgent-per-step wastes two model round-trips per step — solved
late by `DeterministicChain`).

---

## 3. Recommendations for the successor project

1. **Define done before writing code.** A golden corpus of 5–10 real documents
   (including the known hard ones: TOC-bearing, long tables, Arabic content) with
   reviewed expected outputs, and a scoring script: coverage ratio, placeholder
   consistency, rejection rate, REVIEW ratio. CI runs it on every PR. Every prompt
   or pipeline change must move a number.
2. **Port v10, don't restart.** v10 is the convergent design and it demonstrably runs
   end-to-end. Carry over the pipeline shape (§1 "convergent core") and its contracts;
   leave behind the v1–v9 folders as history. Rewrites lose lessons — that is the
   single clearest pattern in this repository.
3. **Make the model boundary a real abstraction.** One `llm_client` interface with
   swappable providers; use a model with native structured outputs / tool-use for the
   decision stages, keep JSON-repair as fallback, not as a design driver. Budget the
   defensive-plumbing tax honestly when choosing a "cheap" model.
4. **Anchor by ID, not by text.** The pipeline already assigns stable leaf IDs; the
   Add-in payload should apply changes by leaf ID + offset ranges computed
   deterministically server-side. Retire the whole-unit string-equality law and its
   rejection tax.
5. **Close the human loop first.** Implement the targeted partial re-run
   (stage-scoped, per-section) that v10's intervention tools already hint at. The
   product is not "one perfect automatic run"; it is "a run plus a fast convergence
   loop". This is the highest-leverage unfinished work.
6. **One repo, real branches, real tests.** No `_vN` sibling folders. Feature branches
   + PRs; docs generated or CI-checked against code; every tool block gets a unit test
   with a stubbed LLM and at least one recorded real fixture; delete dead code in the
   same PR that obsoletes it.
7. **Keep the discipline that worked.** State-key registry, tool-block layout,
   Reads:/Writes: docstring contracts, per-call disk diagnostics, deterministic
   validation with typed rejection reasons, integrity rule for prompts (no
   corpus-tailored hints) — these are the parts of v9/v10 that made ten generations
   of archaeology possible at all.

---

*Compiled 2026-07-28 from full-code analysis of all version folders, their run
artifacts (`.adk/runs`, `.runs`, `_session_dump*`), and in-code documentation.*
