# Open Risks — tracked from day one

Ranked. Each risk has an owner mitigation in the design; none is "solved" until
the golden-corpus metric attached to it says so.

| # | Risk | Mitigation in design | Metric that closes it |
|---|------|----------------------|-----------------------|
| 1 | **Inventory recall** — an actor the stage-3 LLM never extracts is invisible to the surface scan and survives anonymization | Deterministic candidate sweep (dates, decision numbers, quoted/proper-name patterns) forces classification of pattern hits; decide-stage "newly noticed" channel → REVIEW | Recall vs human-annotated mention lists on the golden corpus |
| 2 | **Text outside the body** — headers, footers, footnotes, text boxes, comments, doc properties are separate OOXML parts; the org name usually lives in the page header. No v1–v10 generation handled this | `ingestion/ooxml` walks header*/footer*/footnotes/core.xml parts, not just document.xml | Golden doc with branded header must produce header leaves |
| 3 | **Semantic re-identification** — unique facts identify the org without naming it ("أكبر صندوق سيادي…"). No placeholder scheme fixes this | Final cheap LLM sweep: "can an outside reader guess the org? where?" → REVIEW items; honest residual-risk note in the UX | Red-team pass on golden corpus outputs |
| 4 | **Arabic surface matching** — prefixes (و/ف/ب/ل/ك/ال), diacritics, ة/ه, mixed AR/EN | Normalization with index mapping + prefix-tolerant word-bounded matching (implemented, tested) | Zero missed-mention findings on Arabic golden docs |
| 5 | **Office.js anchoring limits** — content-control wrapping cost on 200-page docs; some structures resist wrapping | Feasibility spike is Milestone 3; fallback: bookmarks or server-side ID mapping | Spike report on the largest real doc |
| 6 | **Golden corpus is human work** — all measurement assumes 5–10 annotated real documents | First milestone, before feature work | Corpus exists and CI runs it |
| 7 | **Rewrite temptation** — the v1–v10 pattern: when a stage struggles, the pipeline gets redesigned | Rule: fix the block, move the metric. Architecture changes require a failing metric that block-level fixes could not move | — (process rule) |
