# RUNBOOK — setting up and running the full experience

Everything needed to go from a fresh clone to anonymizing a document inside
Word. All commands are PowerShell (Windows); Linux/macOS equivalents differ
only in venv activation and `copy`→`cp`.

> **Shortcut:** double-click **`start-all.bat`** in the repo root — it opens
> the agent (self-provisioning on first run) and the add-in server in two
> windows. `start-agent.bat` / `start-addin.bat` run them individually.
> Sections 1–3 below explain what those launchers automate.

## 0. Prerequisites

- Python **3.11+** (`python --version`)
- Microsoft Word desktop (2021 or Microsoft 365 recommended — tracked-changes
  apply needs WordApi 1.4)
- A Groq API key (without it the agent runs in **stub mode**: pipeline works,
  but the inventory extracts 0 actors)

## 1. Agent setup

```powershell
git clone https://github.com/enosama2006/pif_artifactory_project_new.git
cd pif_artifactory_project_new\agent

python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

copy .env.example .env
notepad .env          # → GROQ_API_KEY=gsk_...   (loaded automatically at startup)
```

Verify (no network, no key needed):

```powershell
python -m pytest tests/ -v        # expect: all tests pass
```

## 2. Running the agent

Three entry points, same pipeline:

| Mode | Command | Use for |
|------|---------|---------|
| **HTTP API** (the add-in path) | `uvicorn app.api.routes:app --port 8080` (from `agent/`) | The Word experience |
| ADK web UI | `adk web` (from the **repo root**), pick `agent`, send a `.docx` path | Inspecting sessions/state |
| CLI | `python run_local.py path\to\document.docx` (from `agent/`) | Quick terminal runs |

Health check: `curl http://localhost:8080/health` →
`{"ok":true,"llm_mode":"groq"|"stub","model":...}` — `llm_mode` tells you
whether the key was picked up.

## 3. Add-in setup (one-time sideload)

Serve the taskpane (second terminal):

```powershell
cd pif_artifactory_project_new\addin
python -m http.server 3000
```

Sideload via a trusted shared-folder catalog:

1. Create `C:\addin-share`, copy `addin\manifest.xml` into it.
2. Right-click the folder → Properties → Sharing → Share → note `\\PC-NAME\addin-share`.
3. Word → File → Options → Trust Center → Trust Center Settings →
   **Trusted Add-in Catalogs** → add that path → tick *Show in Menu* → OK →
   restart Word.
4. Word → Insert → My Add-ins → **SHARED FOLDER** → Anonymizer.

Alternative (npm tooling): `npm i -g office-addin-debugging` then
`npx office-addin-debugging start addin\manifest.xml desktop`.

## 4. The full flow in Word

1. Open your document → ribbon → **Anonymizer** — the pane opens; the header
   pill shows live agent status (`online · groq` / `online · stub (no key)` /
   `offline`, refreshed every 8 s).
2. **Run anonymization** — the pane shows each pipeline stage live
   (ingest → inventory → surface scan → classify+rules → decide → assemble),
   then metrics chips (leaves, coverage, rewrites, review, silent losses).
3. Review the **identity dictionary** first — renaming a placeholder there
   updates every pending change at once (recorded as an intervention).
4. Work the **review queue** (items the pipeline refused to decide silently).
5. **Apply all** — accepted changes are written as tracked changes through
   the `anz:` content-control anchors (never text search). Use Word's Review
   tab to accept/reject individually.
6. **Clean anchors** when done.

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Pane pill says `offline` | Agent not running or wrong URL — start uvicorn, then **Check** |
| Pill says `stub (no key)` | `.env` not read: file must be exactly `agent\.env`, line `GROQ_API_KEY=...`, no quotes/spaces; restart uvicorn |
| `adk web` lists nothing | Run it from the **repo root**, not from inside `agent/` |
| Add-in absent from SHARED FOLDER tab | Catalog path wrong or Word not restarted; re-check Trust Center |
| Run errors with `json_validate_failed` / connection resets | Known transient Groq behaviour — the adapter retries a ~90 s backoff ladder automatically; if it still fails, rerun |
| `anchor not found` on apply | Anchors were cleaned or the document changed structurally — rerun anonymization |
| Port 8080/3000 busy | `uvicorn ... --port 8081` and update the pane's server URL / serve add-in on another port and update `manifest.xml` |

## 6. Where results live

- Response payload: applied live by the add-in (leaf-ID anchored spans).
- SQLite `agent/anonymizer.db`: runs + every user intervention (durable audit).
- Full state (actors, links, decisions, metrics): ADK session state when run
  via `adk web`; the `/runs/{id}` response when run via the API.
