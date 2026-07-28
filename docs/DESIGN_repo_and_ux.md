# Successor Repo Blueprint — Two Independent Folders, Block-Based Agent, Review UX

Extends `DESIGN_next_project.md` (pipeline internals) with: the repository layout,
the agent↔add-in contract, the ingestion-gateway block design, and the full user
review experience including how user choices are persisted.

## 1. Repository layout — two fully independent folders

```
anonymizer/                        (new GitHub repo)
├── agent/                         # Python service — owns its own lifecycle
│   ├── pyproject.toml
│   ├── README.md
│   ├── app/
│   │   ├── api/                   # FastAPI routers (the ONLY public surface)
│   │   ├── ingestion/             # gateway blocks — one folder per input type
│   │   │   ├── _contract.py       # UnifiedDocument (USD) — the single internal input contract
│   │   │   └── ooxml/             # today: OOXML → USD   (later: pdf/, html/, txt/ …)
│   │   ├── pipeline/              # one folder per stage block
│   │   │   ├── inventory/         #   each block: contract.py + block.py + prompt.py? + tests/
│   │   │   ├── surface_scan/
│   │   │   ├── decide/
│   │   │   └── validate_assemble/
│   │   ├── llm/                   # provider-agnostic client; groq/anthropic adapters quarantined here
│   │   ├── store/                 # SQLAlchemy models + idempotent writers/readers
│   │   └── config.py
│   ├── openapi.json               # generated from FastAPI — THE contract artifact
│   └── tests/  golden/            # golden corpus + CI metrics
└── addin/                         # Office.js Word Add-in — TypeScript + React
    ├── package.json
    ├── manifest.xml
    └── src/
        ├── api/                   # typed client GENERATED from agent/openapi.json
        ├── anchoring/             # content-control tagging of the open document
        ├── screens/               # Progress / Dictionary / Changes / Review
        └── state/
```

Independence rules: no shared source, no shared build. The only coupling is
`openapi.json`: the add-in regenerates its typed client from the agent's URL
(`/openapi.json`) in CI, so drift breaks the build, not the user.

## 2. Ingestion gateway — extensible by construction

`ingestion/_contract.py` defines the **UnifiedDocument (USD)**: an ordered flat list
of leaves `{leaf_id, kind, text, section_path, table_addr?}` plus a minimal section
tree. Every ingestion block is `(raw bytes, mime) → USD` and registers itself in a
format registry keyed by sniffed type. The pipeline imports **only** the USD contract
— never a format module. Adding PDF later = one new folder + registry entry; zero
pipeline changes. (This generalises v9's hard-won lesson: never let a parser's type
vocabulary cross into the pipeline.)

## 3. The innovation that retires the mapping problem: client-side anchoring

Before extracting anything, the add-in walks the open document once and wraps each
paragraph/heading/table cell in a **Word content control tagged with the leaf ID**
(`tag = "anz:L_000123"`). Then it extracts the OOXML (which now carries the tags) and
uploads it. Consequences:

- The agent's leaf IDs and the live document's anchors are the same namespace —
  applying a change is `getContentControlByTag(id).insertText(...)`, no text search,
  no whole-unit string-equality law, no off-target matches, ever.
- The user can keep editing the document while the run executes; anchors survive
  edits (content controls move with their content).
- On finish, controls can be untagged/removed cleanly.

## 4. API contract (agent's only public surface)

```
POST /documents                       multipart upload → {document_id}
POST /documents/{id}/runs             start pipeline → {run_id}
GET  /runs/{id}/events                SSE progress (stage started/finished, counts)
GET  /runs/{id}/inventory             actors + roles + placeholders + mention counts
GET  /runs/{id}/decisions?status=     leaf decisions (REWRITE/KEEP/REVIEW), paged
POST /runs/{id}/interventions         the ONE write endpoint for every user action
POST /runs/{id}/rerun                 targeted partial re-run {actor_ids?|leaf_ids?}
GET  /runs/{id}/payload               final apply-payload keyed by leaf ID
```

`POST /interventions` body: `{type, target, payload, note?}` with
`type ∈ {rename_placeholder, merge_actors, correct_role, add_surface, ignore_actor,
accept_leaf, reject_leaf, edit_leaf, annotate}`. Every call is a durable DB row —
this single endpoint IS the "store the user's choices/edits/notes" requirement.

## 5. The review UX — direct manipulation, not conversation

**Decision: the user does not chat with the agent to review. Every review action is a
click/edit on structured suggestions; each action is an API call.** Rationale: a run
produces 100–400 decisions — conversing about them one by one is slower than the
manual work it replaces; free-text intent must be re-parsed by an LLM (a new error
source) while a click is unambiguous; and structured actions are exactly what the DB
and the partial-rerun engine need. A chat pane can be ADDED later for *explanations*
("لماذا أخفيت هذا؟") and bulk commands, as sugar over the same interventions API —
it is not the review mechanism.

### The journey (add-in screens)

1. **Start** — "ابدأ إخفاء الهوية" → anchoring pass → upload → run. Progress screen
   driven by SSE: تحليل البنية ← جرد الهويات ← مسح المواضع ← القرارات ← التحقق.
2. **Dictionary screen (الهويات) — review identities before texts.**
   Table: placeholder · role · #mentions · sample. Inline actions: rename the
   placeholder text, merge two actors, correct role, add a missed variant, "أبقِه
   ظاهرًا". One rename here fixes every mention document-wide — this screen is what
   guarantees the user's #1 pain (one name → one placeholder everywhere).
   Any edit marks affected leaves stale → banner: "تحديث ١٤ موضعًا" → `POST /rerun`.
3. **Changes screen (التغييرات)** — decisions grouped by section, REVIEW items pinned
   on top with their reason. Each card: original / suggestion / reason + accept ✓,
   reject ✗, edit ✎ (opens the suggestion as editable text), note 📝. Clicking a card
   scrolls Word to the anchored content control and highlights it. Bulk bar:
   "accept all in section", "accept all for this actor".
4. **Apply** — accepted decisions are written through the anchors **as Word tracked
   changes**, so Word's native review (قبول/رفض التغييرات) remains the final,
   familiar safety net. Reject/accept in Word is read back and recorded too.
5. **Converge** — REVIEW queue empties over loops of (2)–(4); a final
   "المستند مجرّد ✅" state offers: export clean copy / save dictionary to the
   organisation library.

### What the DB remembers (reuse across documents)

- Every intervention row (who, when, what, note) — the audit trail.
- The **organisation dictionary**: actors + roles + chosen placeholders survive the
  run. The next document from the same org seeds stage-2 inventory with it, so
  placeholders are consistent org-wide and the user's corrections never repeat.
- Accepted/rejected patterns become a feedback corpus for prompt iteration in CI.

## 6. Build order (suggested milestones)

1. `agent/ingestion/ooxml` + USD + `store` + `POST /documents` — golden corpus green on parsing (coverage invariant).
2. Pipeline blocks 2–5 behind the API; `_e2e` with real assertions on the corpus.
3. `addin/anchoring` + upload + progress (SSE).
4. Dictionary screen + interventions + partial rerun.
5. Changes screen + tracked-changes apply.
6. Org dictionary reuse; then (optional) explanation chat; then new ingestion formats.
