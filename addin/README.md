# addin/ — Word Add-in (plain Office.js, no build step)

Working MVP: anchoring pass → upload to the agent → dictionary + review +
changes screens → apply as tracked changes via content-control tags.
No React, no webpack — `taskpane.html` + `taskpane.js` + Office.js from CDN.

## Run the full experience (Windows + Word desktop)

**Terminal 1 — the agent API** (needs `GROQ_API_KEY` in `agent/.env` for real runs):

```powershell
cd agent
uvicorn app.api.routes:app --port 8080
```

**Terminal 2 — serve the taskpane:**

```powershell
cd addin
python -m http.server 3000
```

**Sideload into Word (once):**

1. Create a folder, e.g. `C:\addin-share`, copy `manifest.xml` into it.
2. Right-click the folder → Properties → Sharing → Share → note the path `\\PC-NAME\addin-share`.
3. Word → File → Options → Trust Center → Trust Center Settings →
   **Trusted Add-in Catalogs** → add `\\PC-NAME\addin-share` → tick
   *Show in Menu* → OK → restart Word.
4. Word → Insert → My Add-ins → **SHARED FOLDER** → Anonymizer — إخفاء الهوية.

(Alternative without folder sharing: `npm i -g office-addin-debugging` then
`npx office-addin-debugging start manifest.xml desktop`.)

**Use it:**

1. Open the document → ribbon button **إخفاء الهوية** → the pane opens.
2. زر «فحص» يتأكد من الوكيل ويعرض وضع النموذج (Groq / تجريبي).
3. «ابدأ التجريد»: يرسّخ الفقرات بوسوم `anz:C_…`، يرسل OOXML، ويعرض
   المقاييس + القاموس + قائمة المراجعة + التغييرات.
4. عدّل المسميات في القاموس (تسجَّل كتدخلات)، ثم «طبّق الكل» — تُكتب
   كتغييرات متعقبة تراجعها بواجهة وورد الأصلية.
5. «أزل وسوم الترسيخ» عند الانتهاء.

## Files

```
manifest.xml     sideload manifest (taskpane at http://localhost:3000)
taskpane.html    RTL Arabic UI (server row, status, dictionary, review, changes)
taskpane.js      anchoring / upload / render / apply / interventions
assets/          ribbon icons
src/             reserved for the TypeScript/React rewrite (Milestone 4+)
```

## Known MVP limits (tracked in docs/RISKS.md)

- Page-header/footer leaves have no anchors (Office.js wraps body paragraphs
  only) — their changes are listed for manual application.
- Anchoring wraps paragraphs, not individual table cells; cell leaves inherit
  the cell-paragraph anchor.
- `changeTrackingMode` needs WordApi 1.4+ (Word 2021/365); on older Word the
  apply still works, just without tracked-changes marks.
