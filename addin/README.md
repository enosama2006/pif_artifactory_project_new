# addin/ — Word Add-in (Office.js + TypeScript + React)

Completely independent from `agent/`; the only coupling is the typed API
client generated from the agent's `/openapi.json` (see `src/api/`).

## The flow (docs/DESIGN_repo_and_ux.md §5)

1. **Anchor** — wrap every paragraph/heading/cell in a content control tagged
   `anz:L_NNNNNN` BEFORE extraction. The agent's leaf IDs and the live
   document share one namespace; apply is `getContentControlByTag`, never
   text search.
2. **Upload & progress** — POST the OOXML; SSE progress per stage.
3. **Dictionary screen first** — review identities (rename/merge/ignore) before
   texts; one rename fixes every mention document-wide; edits trigger a
   targeted partial re-run.
4. **Changes screen** — decisions grouped by section, REVIEW pinned on top;
   accept ✓ / reject ✗ / edit ✎ / note 📝 per card; card click scrolls Word to
   the anchor. Every action = one `POST /interventions` call.
5. **Apply** — accepted decisions written through anchors as tracked changes;
   Word's native accept/reject stays the final safety net.

## Layout

```
src/
├── api/         generated client + SSE helper
├── anchoring/   content-control tagging/untagging passes
├── screens/     Progress / Dictionary / Changes / Done
└── state/       run state, intervention queue (offline-tolerant)
```

## Status

Skeleton only — Milestone 3 (see docs/DESIGN_repo_and_ux.md §6). The anchoring
feasibility spike on a 200-page document is the first task here (RISK #5).
