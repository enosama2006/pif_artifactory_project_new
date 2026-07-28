# Golden corpus — the project's definition of done

Place 5–10 REAL institutional documents here (after clearance), each with:

```
golden/
├── doc_01/
│   ├── source.docx
│   ├── expected_actors.json     # human-annotated: every actor + every mention
│   └── notes.md                 # why this doc is hard (TOC, long tables, header branding…)
```

CI metrics ratcheted on every PR:
- **leaf coverage** = decided/total (must be 1.0 by construction — regression test)
- **surface recall** vs `expected_actors.json` mention lists ← RISK #1's metric
- **placeholder consistency violations** = 0
- **REVIEW ratio** (drives prompt iteration)
- **interventions-to-clean** — the true product metric

Must-include hard cases: a TOC-bearing doc, a long-table doc, an Arabic-only
doc, a mixed AR/EN doc, and one with the org name in the page header.
