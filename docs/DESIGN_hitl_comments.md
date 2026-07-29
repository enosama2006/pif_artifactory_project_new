# HITL comments + Redo — technical architecture (phase 1: bound path)

Owner's goal (2026-07-29): the initial run occasionally drops an entity;
Human-in-the-loop must ENRICH the run — a comment fixes the gap in THIS
document AND becomes stored knowledge for future ones. Three real cases:

1. **Missed surface** — select text in Word + comment → the agent extracts
   the surface, finds every sibling mention, regenerates affected rewrites.
2. **Dictionary edit** — a change to an actor must propagate to the DB and
   every linked text.
3. **Unconvincing rewrite** — a comment says what the text/table row should
   become; the agent redoes that leaf on a path different from the initial
   run.

## Principles (same iron rules as the pipeline)

- **The LLM is an interpreter, never an executor.** One arbiter call turns
  one comment into ONE operation from a CLOSED set. Anything outside the
  enum, or referencing an unknown actor/leaf, is SHOWN to the user and never
  executed (the invented-placeholder philosophy).
- **Everything after the arbiter is deterministic code**: re-scan, cascade
  recompute, assemble. The only other LLM touch is a decide mini-batch for
  the affected leaves — with the user's comment attached as binding
  guidance.
- **No silent loss**: untouched leaves keep their decisions verbatim; the
  redo report lists per comment what happened (op applied / error); the UI
  badges every card the redo changed.

## Data flow

```
Word selection ──┐
dictionary row ──┼─ bind ─► POST /runs/{id}/comments ─► resolve → pending
change card ─────┘                                      (leaf_id/actor_id)
                                                             │
                                    🔁 Redo  ─► POST /runs/{id}/redo
                                                             │
                     per comment: arbiter (LLM, CLOSED op) ──┤
                     validate_op → invalid ⇒ report only ────┤
                                                             ▼
                     apply_ops on the dictionary (pure code)
                                                             ▼
                     re-scan ALL leaves (pure code) → new links
                     recompute cascade (pure code, stored classifications)
                                                             ▼
                     affected = link-set changed ∪ rewrite_leaf targets
                     decide mini-batch (LLM) ONLY for affected,
                     with per-leaf USER GUIDANCE section
                                                             ▼
                     merge decisions → validate_and_assemble (full gates)
                     edit_leaf overrides applied on the payload
                                                             ▼
                     diff old vs new payload → updated_leaf_ids
                     result replaced in the run record + redo_report
```

## Closed arbiter operations (`app/pipeline/arbiter/ops.py`)

| op | fields | effect |
|----|--------|--------|
| `add_surface` | `surface`, `actor_id` OR `new_actor{name,kind,role}` | surface joins the actor's variants (both "&"/"and" forms); new actor is minted through the SAME merge machinery as the run |
| `rename_placeholder` | `actor_id`, `placeholder` (`<snake_case>`) | placeholder replaced; uniqueness enforced |
| `merge_actors` | `keep`, `drop` (actor ids) | variants/roles union into `keep`; links remap on re-scan |
| `correct_role` | `actor_id`, `role` | role prepended (wins future minting) |
| `ignore_actor` | `actor_id` | status=ignored; excluded from scan → its rewrites dissolve |
| `edit_leaf` | `leaf_id`, `after` | the user's wording is final — applied on the payload AFTER the gates |
| `rewrite_leaf` | `leaf_id`, `guidance` | leaf forced into the decide mini-batch with the guidance attached |
| `comment` | `note` | no-op — stored as guidance memory only |

Validation checks ids against the run state and the placeholder format;
`validate_op` returns `(op, "")` or `(None, error)` — errors go into the
redo report verbatim.

## Comment → leaf resolution (`resolve_bind`, deterministic)

Priority order, first hit wins; ambiguity returns unresolved (visible,
never guessed):
1. explicit `leaf_id` (comment on a change card);
2. `anchor` — the selection's `parentContentControl.tag` (`anz:…`) is the
   same join key the payload uses;
3. `paragraph_text` — normalized (scan's `normalize`) equality against leaf
   texts; if several, the one containing `selected_text`;
4. `selected_text` — normalized containment, only if exactly ONE leaf
   matches.
An unresolved bind is still accepted (the arbiter sees the raw comment),
but the report says the leaf could not be pinned.

## Partial re-run (`app/pipeline/redo/engine.py`)

- `apply_ops(actors, ops)` mutates the dictionary only.
- Re-scan is over ALL leaves — that is what finds the siblings of a newly
  added surface with zero LLM cost.
- Cascade recompute reuses the run's stored `classifications` (the enum
  LLM call from the initial run) — `compute_cascade` is now a shared
  function in `_adk/stages.py` used by both paths.
- `affected` = leaves whose link SET changed (by actor+span) ∪ explicit
  `rewrite_leaf` targets. A pure rename touches no links: zero decide
  calls, assemble alone re-renders every placeholder.
- The decide mini-batch reuses `build_decide_prompt` with a new optional
  `user_guidance` payload section ({leaf_id: comment text}) — absent for
  the initial run, so the original path is untouched.
- Reconcile + `validate_and_assemble` run with the full gate set;
  decisions for unaffected leaves are carried over verbatim.

## API (routes.py, version 0.5.0)

- `POST /runs/{run_id}/comments` `{text, bind:{leaf_id?|actor_id?|anchor?,
  paragraph_text?, selected_text?}}` → `{ok, comment_id, resolved}`;
  stored in the run record AND as a durable `comment` intervention row.
- `DELETE /runs/{run_id}/comments/{comment_id}` — remove a pending comment.
- `POST /runs/{run_id}/redo` → runs the flow above, replaces the run's
  `result`, returns `{ok, redo_report, updated_leaf_ids, metrics}`.
  Pending comments move to `processed_comments` (kept for diagnostics).

## Add-in UX (v0.8.0)

- One comment box in a new "Comments" section. Three binding gestures:
  - **💬 on a dictionary row** → chip "bound to actor X";
  - **💬 on a change card** → chip "bound to leaf L_NNNNNN";
  - **"💬 Comment on selection"** → reads the Word selection (text +
    parent paragraph + anchor tag) at submit time — case 1 without typing
    the surface.
- Pending drawer: each comment shows its text, bind label, resolution
  state, and ✕ remove. The counter lives on the "🔁 Redo with comments (N)"
  button — one click processes everything.
- After redo: cards whose leaf is in `updated_leaf_ids` carry an
  "↻ updated by your comment" badge; the redo report (op per comment,
  errors verbatim) prints to the log and ships in diagnostics v3.

## Memory (phase 1 scope)

Every comment is a durable intervention row (`comment` type) keyed by run;
the actors table already persists per run. Cross-document seeding
(org dictionary reuse, BACKLOG item 4) will read from both — this phase
only guarantees the raw material is recorded.

## Phase 2 (not in this change-set)

Free comments → refine call → USER GUIDANCE prompt section on
portrait/inventory/decide + previous dictionary as a binding seed → full
re-run.
