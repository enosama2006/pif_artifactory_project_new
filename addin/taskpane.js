/* Anonymizer taskpane — plain Office.js, no build step.
 *
 * Flow (docs/DESIGN_repo_and_ux.md):
 *  1. ANCHOR   wrap every paragraph in a content control tagged anz:C_NNNNN —
 *              the agent reads the tags from the OOXML, so applying is by tag
 *              lookup, never text search.
 *  2. UPLOAD   getOoxml() → POST {server}/runs, then poll GET /runs/{id} to
 *              render live stage progress.
 *  3. REVIEW   dictionary first (a rename fixes every mention), then the
 *              changes list; every user action → POST /interventions.
 *  4. APPLY    tracked changes through the anchors; Word's native
 *              accept/reject stays the final safety net.
 */
/* global Office, Word, fetch, document */

const UI_VERSION = "0.8.2";        // bump when the pane changes — shown in the header
let RUN = null;                    // completed run result
let COMMENTS = [];                 // pending HITL comments (mirror of the server)
let PENDING_BIND = null;           // {bind:{...}, label} set by the 💬 buttons
let SERVER = "http://localhost:8080";
const STAGE_LABELS = {
  ingest: "Ingest — leaf inventory",
  portrait: "Portrait — document context (LLM)",
  inventory: "Actor inventory (LLM)",
  surface_scan: "Surface scan",
  classify_rules: "Classify + breakage rules",
  decide: "Decide (LLM)",
  assemble: "Validate & assemble",
};

const $ = (id) => document.getElementById(id);
const show = (id, on = true) => { $(id).style.display = on ? "block" : "none"; };
const log = (msg) => {
  const el = $("log");
  el.style.display = "block";
  el.textContent += (el.textContent ? "\n" : "") + msg;
  el.scrollTop = el.scrollHeight;
};

/* Operation log — every search/apply/clean operation is recorded with its
 * outcome, so nothing fails silently (run-5 owner ask). Mirrored into the
 * visible log and shipped inside the diagnostics report. */
const OPLOG = [];
function op(kind, msg) {
  OPLOG.push({ t: new Date().toISOString().slice(11, 23), kind, msg });
  log(`[${kind}] ${msg}`);
}

Office.onReady(() => {
  $("uiVersion").textContent = "ui v" + UI_VERSION;
  log("Taskpane loaded — ui v" + UI_VERSION);
  checkHealth();
  setInterval(checkHealth, 8000);   // live agent status in the header pill
});

/* Parse check — ingest-only dry run (no LLM): shows what the OOXML parser
 * extracts so document-vs-extraction questions are answered BEFORE a run
 * (owner ask, run 6). Duplicate texts are flagged; full leaves land in the
 * diagnostics box for pasting. */
async function parseCheck() {
  SERVER = $("serverUrl").value.replace(/\/+$/, "");
  try {
    log("Parse check: extracting OOXML…");
    let ooxml = "";
    await Word.run(async (ctx) => {
      const x = ctx.document.body.getOoxml();
      await ctx.sync();
      ooxml = x.value;
    });
    const r = await (await fetch(SERVER + "/parse", {
      method: "POST", headers: { "Content-Type": "application/xml" }, body: ooxml,
    })).json();
    if (!r.ok) throw new Error(r.error || "parse failed");
    op("parse", `${r.leaf_count} leaves — ` +
       Object.entries(r.kinds).map(([k, n]) => `${k}: ${n}`).join(", "));
    const seen = {};
    (r.leaves || []).forEach(l => {
      const t = (l.text || "").trim();
      if (t) (seen[t] = seen[t] || []).push(l.leaf_id);
    });
    const dups = Object.entries(seen).filter(([, ids]) => ids.length > 1);
    op("parse", dups.length
      ? `${dups.length} duplicated text(s): ` + dups.slice(0, 8)
          .map(([t, ids]) => `"${t.slice(0, 30)}" ×${ids.length} (${ids.join(",")})`).join("; ")
      : "no duplicated texts — extraction is clean.");
    const box = $("diagBox");
    box.style.display = "block";
    box.value = "=== PARSE CHECK ===\n" + JSON.stringify(r, null, 1);
    log("Full parsed leaves are in the box below — copy/paste them for review.");
  } catch (e) { log("ERROR parse check: " + fmtErr(e)); }
}

async function checkHealth(verbose = false) {
  SERVER = $("serverUrl").value.replace(/\/+$/, "");
  const dot = $("agentDot"), state = $("agentState");
  try {
    const r = await (await fetch(SERVER + "/health", { cache: "no-store" })).json();
    dot.className = "dot on";
    state.textContent = `online · ${r.llm_mode}${r.llm_mode === "stub" ? " (no key)" : ""}`;
    if (verbose) log(`Agent online — mode=${r.llm_mode}, model=${r.model}, v${r.version}`);
  } catch {
    dot.className = "dot off";
    state.textContent = "offline";
    if (verbose) log(`Agent unreachable at ${SERVER}. Start it with:\n  uvicorn app.api.routes:app --port 8080`);
  }
}

/* ── 1. anchoring pass ────────────────────────────────────────────────────── */

/* Fault-tolerant anchoring:
 * - small chunks, each in its own Word.run (one bad paragraph can't kill all);
 * - a failing chunk is retried paragraph-by-paragraph, stubborn ones skipped
 *   (empty paragraphs, field/TOC content, the trailing end mark…);
 * - no `appearance` override (Hidden throws GeneralException on some builds);
 * - already-anchored paragraphs (anz:*) are left untouched, so reruns are safe.
 */
const ANCHOR_CHUNK = 25;
let anchorSeq = 0;

async function anchorRange(start, end) {
  let anchored = 0, skipped = 0;
  await Word.run(async (ctx) => {
    const paras = ctx.document.body.paragraphs;
    paras.load("items/text");
    await ctx.sync();
    const slice = paras.items.slice(start, end);
    slice.forEach(p => p.parentContentControlOrNullObject.load("isNullObject,tag"));
    await ctx.sync();
    for (const p of slice) {
      const text = (p.text || "").trim();
      const pcc = p.parentContentControlOrNullObject;
      const already = !pcc.isNullObject && (pcc.tag || "").startsWith("anz:");
      if (!text || already) { skipped++; continue; }
      const cc = p.insertContentControl();
      cc.tag = "anz:C_" + String(++anchorSeq).padStart(5, "0");
      anchored++;
    }
    await ctx.sync();
  });
  return { anchored, skipped };
}

/* Auto-clean BEFORE the agent receives the document (owner rule): remove
 * stale anz anchors AND unwrap every other content control (built-in cover
 * dropdowns like "Policy Owner Division", legacy sdt boxes) keeping their
 * text — those boxes refused anchoring, hid their text from search, and
 * blocked replacement (run-5/6 finding: cover "Chief of Staff"). After
 * unwrapping, every paragraph is plain text: anchorable, findable,
 * replaceable without constraints. */
async function autoCleanAnchors() {
  let anz = 0, boxes = 0, failed = 0;
  const count = (c) => ((c.tag || "").startsWith("anz:") ? anz++ : boxes++);
  try {
    await Word.run(async (ctx) => {
      const ccs = ctx.document.contentControls;
      ccs.load("items/tag,items/title");
      await ctx.sync();
      for (const c of ccs.items) {
        try { c.cannotDelete = false; } catch { /* not lockable on this type */ }
        count(c);
        c.delete(true);                 // true = keep the content as plain text
      }
      await ctx.sync();
    });
  } catch (batchErr) {
    // one refusing control must not keep the rest wrapped — retry one by one
    op("clean", `batch unwrap failed (${fmtErr(batchErr)}) — retrying one by one…`);
    anz = 0; boxes = 0;
    let guard = 600;
    while (guard-- > 0) {
      let remaining = 0, ok = false;
      try {
        await Word.run(async (ctx) => {
          const ccs = ctx.document.contentControls;
          ccs.load("items/tag,items/title");
          await ctx.sync();
          remaining = ccs.items.length;
          if (!remaining) return;
          const c = ccs.items[0];
          try { c.cannotDelete = false; } catch { /* ignore */ }
          c.delete(true);
          await ctx.sync();
          count(c);
          ok = true;
        });
      } catch { /* fall through */ }
      if (!remaining) break;
      if (!ok) { failed = remaining; break; }
    }
  }
  anchorSeq = 0;
  return { anz, boxes, failed };
}

async function anchorDocument() {
  let total = 0;
  let duplicateTags = 0;
  await Word.run(async (ctx) => {
    const paras = ctx.document.body.paragraphs;
    paras.load("items");
    const ccs = ctx.document.contentControls;
    ccs.load("items/tag");
    await ctx.sync();
    total = paras.items.length;
    // Auto-clean runs first, so normally no anz tags survive here; this is a
    // belt-and-braces pass — if cleaning was skipped/failed we still CONTINUE
    // from the highest existing index (restarting at 1 collided with old tags
    // and produced duplicate anchors — real-run 72d2c2e3b84a).
    const seen = {};
    let maxIdx = 0;
    ccs.items.forEach(c => {
      const m = /^anz:C_(\d+)$/.exec(c.tag || "");
      if (!m) return;
      maxIdx = Math.max(maxIdx, parseInt(m[1], 10));
      seen[c.tag] = (seen[c.tag] || 0) + 1;
    });
    duplicateTags = Object.values(seen).filter(n => n > 1).length;
    anchorSeq = maxIdx;
  });
  if (duplicateTags > 0) {
    op("anchor", `WARNING: ${duplicateTags} duplicate anchor tag(s) survived cleaning — ` +
        `apply may hit the wrong paragraph for those.`);
  }

  let anchored = 0, skipped = 0, failed = 0;
  for (let start = 0; start < total; start += ANCHOR_CHUNK) {
    const end = Math.min(start + ANCHOR_CHUNK, total);
    try {
      const r = await anchorRange(start, end);
      anchored += r.anchored; skipped += r.skipped;
    } catch (chunkErr) {
      log(`Chunk ${start}-${end} failed (${fmtErr(chunkErr)}) — retrying one by one…`);
      for (let i = start; i < end; i++) {
        try {
          const r = await anchorRange(i, i + 1);
          anchored += r.anchored; skipped += r.skipped;
        } catch { failed++; }
      }
    }
  }
  return { anchored, skipped, failed, total };
}

/* ── 2. run + live progress ───────────────────────────────────────────────── */

function renderPipeline(stageNames, rec) {
  show("pipeline");
  const done = new Set((rec?.stages || []).map(s => s.stage));
  const msgs = Object.fromEntries((rec?.stages || []).map(s => [s.stage, s.message]));
  $("pipeline").innerHTML = stageNames.map(name => {
    let cls = "step", ico = "", msg = msgs[name] || "";
    if (done.has(name)) { cls += " done"; ico = "✓"; }
    else if (rec?.current_stage === name) {
      cls += " current";
      msg = rec?.detail || "working…";        // live sub-stage detail (chunk x/y)
    }
    else if (rec?.status === "error" && rec?.error?.startsWith(name)) { cls += " error"; ico = "!"; }
    return `<div class="${cls}"><span class="ico">${ico}</span>` +
           `<span>${STAGE_LABELS[name] || name}</span>` +
           `<span class="msg" title="${esc(msg)}">${esc(msg)}</span></div>`;
  }).join("");
}

async function runPipeline() {
  SERVER = $("serverUrl").value.replace(/\/+$/, "");
  $("runBtn").disabled = true;
  ["secDict", "secReview", "secChanges"].forEach(s => show(s, false));
  show("metrics", false);
  try {
    // Owner rule (run 5): the document must be CLEAN before the agent
    // receives it — stale anchors from earlier runs are removed automatically.
    try {
      const c = await autoCleanAnchors();
      op("clean", `${c.anz} stale anchor(s) removed, ${c.boxes} control box(es) unwrapped ` +
         `(text kept)` + (c.failed ? `, ${c.failed} control(s) refused — their text may resist apply` : "") +
         ` — document is clean before the agent receives it.`);
    } catch (cleanErr) {
      op("clean", "WARNING: auto-clean failed (" + fmtErr(cleanErr) +
         ") — continuing; anchor numbering will avoid collisions.");
    }

    log("Anchoring paragraphs with content controls…");
    let a = { anchored: 0, skipped: 0, failed: 0, total: 0 };
    try {
      a = await anchorDocument();
      op("anchor", `${a.anchored}/${a.total} paragraph(s) anchored` +
          (a.skipped ? `, ${a.skipped} skipped (empty/already tagged)` : "") +
          (a.failed ? `, ${a.failed} refused by Word (cover/title controls have no anchor)` : "") + ".");
    } catch (anchorErr) {
      // Anchoring is an optimization, not a prerequisite: continue without it —
      // unanchored changes are listed for manual application.
      log("WARNING: anchoring failed entirely (" + fmtErr(anchorErr) +
          ") — continuing without anchors; changes will need manual apply.");
    }

    log("Extracting OOXML and starting the run…");
    let ooxml = "";
    await Word.run(async (ctx) => {
      const x = ctx.document.body.getOoxml();
      await ctx.sync();
      ooxml = x.value;
    });

    const start = await (await fetch(SERVER + "/runs", {
      method: "POST",
      headers: { "Content-Type": "application/xml" },
      body: ooxml,
    })).json();
    if (!start.ok) throw new Error(start.error || "failed to start the run");
    const stageNames = start.stage_names || Object.keys(STAGE_LABELS);
    renderPipeline(stageNames, start);

    // poll for live status
    let rec = start;
    while (rec.status === "running") {
      await new Promise(r => setTimeout(r, 700));
      rec = await (await fetch(`${SERVER}/runs/${start.run_id}`, { cache: "no-store" })).json();
      renderPipeline(stageNames, rec);
    }
    if (rec.status === "error") throw new Error(rec.error || "run failed");

    RUN = { run_id: rec.run_id, llm_mode: rec.llm_mode, ...rec.result };
    COMMENTS = [];           // a fresh run starts with an empty comment drawer
    PENDING_BIND = null;
    $("redoReport").style.display = "none";
    absorbReviewItems();     // REVIEW leaves join the list, in document order
    indexSpans();
    $("diagBtn").disabled = false;
    log(`Run ${rec.run_id} completed (mode=${rec.llm_mode}).`);
    if (rec.llm_mode === "stub")
      log("NOTE: stub mode — put GROQ_API_KEY in agent/.env for real extraction.");
    renderResults();
  } catch (e) {
    log("ERROR: " + fmtErr(e));
  } finally {
    $("runBtn").disabled = false;
  }
}

/* ── 3. results: metrics / dictionary / review / changes ─────────────────── */

/* Map every span to its actor (by the ORIGINAL placeholder) so renames and
 * ignores recompute `after` from spans, never by string surgery on text.
 * Also build actor → mention anchors for the Locate button. */
let MENTIONS = {};       // actor_id -> [{anchor, surface}], document order
let LOCATE_IDX = {};     // actor_id -> cycling cursor

function indexSpans() {
  const byPh = {};
  Object.values(RUN.actors || {}).forEach(a => { byPh[a.placeholder] = a.actor_id; });
  (RUN.payload || []).forEach(p => {
    (p.spans || []).forEach(s => { s.actor_id = byPh[s.replace] || null; });
  });
  MENTIONS = {}; LOCATE_IDX = {};
  const anchorByLeaf = {};
  (RUN.leaves || []).forEach(l => { anchorByLeaf[l.leaf_id] = l.anchor; });
  (RUN.links || []).forEach(l => {
    MENTIONS[l.actor_id] = MENTIONS[l.actor_id] || [];
    MENTIONS[l.actor_id].push({ anchor: anchorByLeaf[l.leaf_id] || null,
                                surface: l.surface });
  });
}

/* Robust select: anchor tag first; if the control is gone or Word refused to
 * wrap that paragraph (built-in cover/title boxes), fall back to a plain
 * text search — safe here because Locate only SELECTS, never replaces. */
/* Floating text boxes (Word shapes) are invisible to anchors AND to
 * body.search — the run-6 cover date lived in one ("Textbox: April 2025" in
 * Word's own navigation). The Shapes API (newer desktop builds) can reach
 * them; every attempt is logged so an unsupported build fails loudly. */
async function withShapeMatch(needle, nth, replacement /* null = select only */) {
  let outcome = null;
  await Word.run(async (ctx) => {
    const shapes = ctx.document.body.shapes;   // throws on builds without the API
    shapes.load("items");
    await ctx.sync();
    const frames = [];
    for (const sh of shapes.items) {
      try {
        const tr = sh.textFrame.textRange;
        tr.load("text");
        frames.push({ sh, tr });
      } catch { /* shape without a text frame */ }
    }
    await ctx.sync();
    const t = needle.trim();
    let pool = frames.filter(f => (f.tr.text || "").trim() === t);
    if (!pool.length) pool = frames.filter(f => (f.tr.text || "").includes(t));
    op("locate", `text boxes scanned: ${frames.length}, matching "${t.slice(0, 40)}": ${pool.length}`);
    if (!pool.length) throw new Error("not found in floating text boxes either");
    const target = pool[Math.min(nth, pool.length - 1)];
    if (replacement === null) {
      target.sh.select();
      outcome = "text box (selected)";
    } else {
      try { ctx.document.changeTrackingMode = "TrackAll"; } catch { /* <1.4 */ }
      target.tr.insertText(replacement, "Replace");
      outcome = "text box (replaced)";
    }
    await ctx.sync();
  });
  return outcome;
}

async function selectByAnchorOrText(anchor, text, nth = 0) {
  if (anchor) {
    try { await withCc(anchor, async (_c, cc) => cc.select()); return "anchor " + anchor; }
    catch (e) {
      op("locate", `anchor ${anchor} lookup failed (${fmtErr(e)}) — falling back to text search`);
    }
  }
  if (!text || !text.trim()) throw new Error("nothing to locate");
  const needle = text.trim().slice(0, 120);
  let how = "";
  await Word.run(async (ctx) => {
    const found = ctx.document.body.search(needle, { matchCase: true });
    found.load("items");
    await ctx.sync();
    op("locate", `text search "${needle.slice(0, 60)}" → ${found.items.length} match(es)`);
    if (!found.items.length) throw new Error("no body match");
    found.items[Math.min(nth, found.items.length - 1)].select();
    how = "text search";
    await ctx.sync();
  }).catch(async (e) => {
    // last resort: floating text boxes
    try { how = await withShapeMatch(text, nth, null); }
    catch (e2) {
      throw new Error(fmtErr(e) + "; shapes: " + fmtErr(e2));
    }
  });
  return how;
}

async function locateActor(actorId) {
  const list = MENTIONS[actorId] || [];
  if (!list.length) { log("No located mentions for this actor."); return; }
  const i = (LOCATE_IDX[actorId] = ((LOCATE_IDX[actorId] ?? -1) + 1) % list.length);
  const m = list[i];
  try {
    // nth: how many earlier mentions share this surface → pick the right hit
    const nth = list.slice(0, i).filter(x => x.surface === m.surface).length;
    const how = await selectByAnchorOrText(m.anchor, m.surface, nth);
    log(`Located ${RUN.actors[actorId].name}: mention ${i + 1}/${list.length} (${how}).`);
  } catch (e) { log("ERROR locating: " + fmtErr(e)); }
}

/* REVIEW leaves become list entries too (owner rule: the list follows the
 * PAPER ORDER and a REVIEW item must be editable in place). Default: the
 * original text stands; Apply All skips it until the human edits it. */
function absorbReviewItems() {
  const leafById = {};
  (RUN.leaves || []).forEach(l => { leafById[l.leaf_id] = l; });
  const have = new Set((RUN.payload || []).map(p => p.leaf_id));
  (RUN.review_queue || []).forEach(r => {
    if (have.has(r.leaf_id)) return;
    const lf = leafById[r.leaf_id] || {};
    RUN.payload.push({ leaf_id: r.leaf_id, anchor: lf.anchor || null,
                       row: lf.row || null, column: lf.col || null,
                       before: r.text, after: r.text, spans: [],
                       review: true, reviewReason: r.reason });
    have.add(r.leaf_id);
  });
  // leaf ids are zero-padded (L_000042) → lexicographic == document order
  RUN.payload.sort((a, b) =>
    a.leaf_id < b.leaf_id ? -1 : a.leaf_id > b.leaf_id ? 1 : 0);
}

function recomputeAfter(p) {
  if (p.review && !p.edited) { p.after = p.before; p.empty = false; return; }
  if (p.edited) { p.empty = false; return; }   // human wording wins over recompute
  let text = p.before;
  const active = (p.spans || []).filter(s =>
    !(s.actor_id && RUN.actors[s.actor_id] && RUN.actors[s.actor_id].ignored));
  active.sort((a, b) => b.start - a.start).forEach(s => {
    const repl = s.actor_id ? RUN.actors[s.actor_id].placeholder : s.replace;
    text = text.slice(0, s.start) + repl + text.slice(s.end);
  });
  p.after = text;
  p.empty = active.length === 0;   // nothing left to change → original text stands
}

function refreshChanges() {
  (RUN.payload || []).forEach(recomputeAfter);
  renderResults();
}

function toggleIgnore(actorId) {
  const a = RUN.actors[actorId];
  a.ignored = !a.ignored;
  intervene("ignore_actor", actorId, { ignored: a.ignored });
  refreshChanges();
  log((a.ignored ? "Ignored: " : "Restored: ") + a.name +
      (a.ignored ? " — its replacements removed; original text stands." : ""));
}

function renderResults() {
  const m = RUN.metrics || {};
  $("metrics").innerHTML =
    chip("Leaves", m.leaves) +
    chip("Coverage", m.coverage, m.coverage === 1 ? "good" : "warn") +
    chip("Rewrites", m.rewrites) +
    chip("Review", m.review, m.review ? "warn" : "good") +
    chip("Silent losses", m.silent_losses, m.silent_losses ? "warn" : "good");
  $("metrics").style.display = "flex";

  const actors = Object.values(RUN.actors || {});
  $("actorCount").textContent = actors.length;
  $("dict").innerHTML = actors.length
    ? "<table><tr><th>Name</th><th>Role(s)</th><th>Placeholder</th><th></th></tr>" +
      actors.map(a => {
        const off = a.ignored;
        return `<tr style="${off ? "opacity:.45;text-decoration:line-through" : ""}">` +
        `<td>${esc(a.name)}</td><td>${esc((a.roles || []).join(", "))}</td>` +
        `<td><input class="ph" id="ph_${a.actor_id}" value="${esc(a.placeholder)}" ${off ? "disabled" : ""}></td>` +
        `<td style="white-space:nowrap">` +
        `<button class="ghost small" onclick="renamePlaceholder('${a.actor_id}')" ${off ? "disabled" : ""}>Save</button> ` +
        `<button class="ghost small" onclick="locateActor('${a.actor_id}')" title="Cycle through this actor's mentions in the document">📍 ${(MENTIONS[a.actor_id] || []).length}</button> ` +
        `<button class="ghost small" onclick="toggleIgnore('${a.actor_id}')">${off ? "↩ Restore" : "🚫 Ignore"}</button> ` +
        `<button class="ghost small" onclick="bindToActor('${a.actor_id}', this)" title="Comment on this actor — the Redo propagates it to the DB and every linked text">💬</button>` +
        `</td></tr>`;
      }).join("") + "</table>"
    : "<div class='muted'>No actors extracted (stub mode? set GROQ_API_KEY and rerun).</div>";
  show("secDict");

  const rq = RUN.review_queue || [];
  $("reviewCount").textContent = rq.length;
  $("review").innerHTML = rq.length
    ? rq.map(r =>
        `<div class="card review"><div>${esc(r.text)}</div>` +
        `<div class="muted">Reason: ${esc(r.reason)}</div></div>`).join("")
    : "<div class='muted'>Nothing needs review 🎉</div>";
  show("secReview");

  // A table ROW is one connected block (owner's rule): its cells render and
  // apply together. The whole list follows the PAPER ORDER (owner rule) —
  // payload is sorted by leaf id, so iterating it IS document order; a row
  // card sits where its first cell sits. REVIEW items render inline with a
  // badge: original text stands until the human edits them.
  const pl = (RUN.payload || []).filter(p => !p.empty);
  $("changeCount").textContent = pl.length;

  const cellHtml = (i) => {
    const p = RUN.payload[i];
    return `<tr id="cell_${i}"><td class="muted">${esc(p.column || "")}</td>` +
      `<td><div class="before">${esc(p.before)}</div>` +
      `<div class="after" id="after_${i}">${esc(p.after)}</div></td>` +
      `<td><button class="ghost small" onclick="editItem(${i})">✎</button></td></tr>`;
  };

  const updatedBadge = (leafIds) =>
    leafIds.some(id => (RUN.updated_leaf_ids || []).includes(id))
      ? `<span class="tag" style="color:var(--accent)">↻ updated by your comment</span>`
      : "";

  const rowCard = (rk, idxs) =>
    `<div class="card" id="row_${esc(rk)}">` +
    `<span class="tag">table row ${esc(rk)}</span>` +
    updatedBadge(idxs.map(j => RUN.payload[j].leaf_id)) +
    `<table style="border:0">${idxs.map(cellHtml).join("")}</table>` +
    `<div class="actions">` +
    `<button class="ghost small" onclick='applyRow(${JSON.stringify(idxs)})'>✓ Apply row</button>` +
    `<button class="ghost small" onclick="goTo(${idxs[0]})">Locate</button>` +
    `<button class="ghost small" onclick='rejectRow(${JSON.stringify(idxs)}, "${esc(rk)}")'>✗ Reject row</button>` +
    `<button class="ghost small" onclick="bindToLeaf(${idxs[0]}, this)" title="Comment on this row — the Redo re-does it per your instruction">💬</button>` +
    `</div></div>`;

  const singleCard = (i) => {
    const p = RUN.payload[i];
    const review = p.review && !p.edited;
    return `<div class="card${p.review ? " review" : ""}" id="chg_${i}">` +
    (p.review ? `<span class="tag" style="color:var(--warn)">REVIEW — ${esc(p.reviewReason || "")}</span>` : "") +
    updatedBadge([p.leaf_id]) +
    `<div class="before"${review ? ' style="text-decoration:none"' : ""}>${esc(p.before)}</div>` +
    (review ? `<div class="muted" id="after_${i}">unchanged until you edit it</div>`
            : `<div class="after" id="after_${i}">${esc(p.after)}</div>`) +
    `<div class="actions">` +
    (review ? "" : `<button class="ghost small" onclick="applyOne(${i})">✓ Apply</button>`) +
    `<button class="ghost small" onclick="editItem(${i})">✎ Edit</button>` +
    `<button class="ghost small" onclick="goTo(${i})">Locate</button>` +
    (review ? "" : `<button class="ghost small" onclick="reject(${i})">✗ Reject</button>`) +
    `<button class="ghost small" onclick="bindToLeaf(${i}, this)" title="Comment on this text — the Redo re-does it per your instruction">💬</button>` +
    `<span class="anchor" style="margin-inline-start:auto">${esc(p.anchor || p.leaf_id)}</span>` +
    `</div></div>`;
  };

  const parts = [];
  const doneRows = new Set();
  pl.forEach(p => {
    const i = RUN.payload.indexOf(p);
    if (p.row) {
      if (doneRows.has(p.row)) return;
      doneRows.add(p.row);
      const idxs = RUN.payload
        .map((q, j) => (!q.empty && q.row === p.row ? j : -1))
        .filter(j => j >= 0);
      parts.push(rowCard(p.row, idxs));
    } else {
      parts.push(singleCard(i));
    }
  });

  $("changes").innerHTML = parts.join("") ||
    "<div class='muted'>No changes proposed.</div>";
  show("secChanges");
  show("secComments");
  renderComments();
}

const chip = (label, value, cls = "") =>
  `<span class="chip ${cls}"><b>${value ?? "—"}</b>${label}</span>`;

/* ── 4. apply via anchors as tracked changes ─────────────────────────────── */

async function withCc(anchor, fn) {
  await Word.run(async (ctx) => {
    try { ctx.document.changeTrackingMode = "TrackAll"; } catch { /* WordApi<1.4 */ }
    const ccs = ctx.document.contentControls.getByTag(anchor);
    ccs.load("items");
    await ctx.sync();
    if (!ccs.items.length) throw new Error("anchor not found: " + anchor);
    await fn(ctx, ccs.items[0]);
    await ctx.sync();
  });
}

/* Which occurrence of this exact text does payload entry i represent, among
 * the ANCHORLESS entries sharing it? (Run-6 finding: two cover "Chief of
 * Staff" cards both jumped to the first search hit.) */
function anchorlessOrdinal(i) {
  const p = RUN.payload[i];
  let nth = 0, total = 0;
  (RUN.payload || []).forEach((q, j) => {
    if (!q.anchor && q.before === p.before) { total++; if (j < i) nth++; }
  });
  return { nth, total };
}

/* Text-search fallback for leaves without an anchor (Word refused to wrap
 * them). Deterministic occurrence matching: replace hit #nth only when the
 * number of matches equals the number of same-text anchorless leaves —
 * any mismatch aborts, because guessing would corrupt text. */
async function replaceByTextOccurrence(p, nth, total) {
  const needle = p.before || "";
  if (!needle.trim()) throw new Error("empty source text");
  if (needle.length > 250)
    throw new Error(`text too long for the search fallback (${needle.length} chars)`);
  await Word.run(async (ctx) => {
    try { ctx.document.changeTrackingMode = "TrackAll"; } catch { /* WordApi<1.4 */ }
    const found = ctx.document.body.search(needle.trim(), { matchCase: true });
    found.load("items");
    await ctx.sync();
    if (found.items.length === 0)
      throw new Error("text not found in body (floating text box or header/footer?)");
    if (found.items.length !== total)
      throw new Error(`ambiguous — ${found.items.length} match(es) for ${total} ` +
                      `pending instance(s), skipped for safety`);
    found.items[nth].insertText(p.after, "Replace");
    await ctx.sync();
  });
  return `text match ${nth + 1}/${total}`;
}

/* Every apply is LOGGED with its outcome — run-5 finding: replacements were
 * failing with no trace. Returns true on success so applyAll can count. */
async function applyOne(i) {
  const p = RUN.payload[i];
  const id = p.leaf_id + (p.anchor ? ` (${p.anchor})` : " (no anchor)");
  if (p.review && !p.edited) {
    op("apply", `SKIP ${id} — REVIEW item, original text stands (edit it to include)`);
    return false;
  }
  let via = null;
  const errs = [];
  if (p.anchor) {
    try {
      await withCc(p.anchor, async (_ctx, cc) => cc.insertText(p.after, "Replace"));
      via = "anchor";
    } catch (e) { errs.push("anchor: " + fmtErr(e)); }
  } else {
    errs.push("no anchor (Word refused to wrap this paragraph)");
  }
  if (!via) {
    const { nth, total } = anchorlessOrdinal(i);
    try {
      via = await replaceByTextOccurrence(p, nth, total);
    } catch (e) {
      errs.push("text fallback: " + fmtErr(e));
      try { via = await withShapeMatch(p.before, nth, p.after); }   // floating box
      catch (e2) { errs.push("shapes: " + fmtErr(e2)); }
    }
  }
  if (via) {
    op("apply", `OK ${id} via ${via}`);
    p.applied = true;
    p.failed = false;
    const el = $("chg_" + i);
    if (el) el.classList.add("applied");
    const cell = $("cell_" + i);
    if (cell) cell.style.opacity = 0.45;
    intervene("accept_leaf", p.leaf_id, { via });
    return true;
  }
  p.failed = true;
  op("apply", `FAILED ${id} — ${errs.join("; ")} — intended text: ${p.after}`);
  return false;
}

async function applyAll() {
  let ok = 0, review = 0;
  const failed = [];
  for (let i = 0; i < (RUN.payload || []).length; i++) {
    const p = RUN.payload[i];
    if (p.empty || p.rejected || p.applied) continue;
    if (p.review && !p.edited) { review++; continue; }   // owner rule: REVIEW
    (await applyOne(i)) ? ok++ : failed.push(p.leaf_id); // never auto-applies
  }
  op("apply", `SUMMARY: ${ok} applied, ${failed.length} failed` +
      (failed.length ? ` — ${failed.join(", ")}` : "") +
      (review ? `; ${review} REVIEW item(s) untouched (edit them to include)` : "") +
      ". Review them in Word's Review tab (tracked changes).");
}

async function goTo(i) {
  const p = RUN.payload[i];
  try {
    const { nth } = anchorlessOrdinal(i);   // run-6: pick THIS instance,
    await selectByAnchorOrText(p.anchor, p.before, p.anchor ? 0 : nth);
  } catch (e) { log("ERROR locating: " + fmtErr(e)); }
}

function reject(i) {
  const p = RUN.payload[i];
  const el = $("chg_" + i);
  if (el) el.classList.add("rejected");
  p.rejected = true;
  intervene("reject_leaf", p.leaf_id, {});
}

/* row = one connected block: its cells apply together, reject together */
async function applyRow(idxs) {
  let ok = 0;
  for (const i of idxs) { if (await applyOne(i)) ok++; }
  const rk = RUN.payload[idxs[0]].row;
  const el = $("row_" + rk);
  if (el) el.classList.add("applied");
  op("apply", `row ${rk}: ${ok}/${idxs.length} cell(s) applied as one block.`);
}

function rejectRow(idxs, rk) {
  idxs.forEach(i => { RUN.payload[i].rejected = true;
                      intervene("reject_leaf", RUN.payload[i].leaf_id, {}); });
  const el = $("row_" + rk);
  if (el) el.classList.add("rejected");
}

/* HITL edit: the human's wording wins and is stored as an intervention */
function editItem(i) {
  const p = RUN.payload[i];
  const el = $("after_" + i);
  if (!el || el.querySelector("textarea")) return;
  const current = p.after;
  el.innerHTML = `<textarea style="width:100%;min-height:52px;font-size:12px;"
    id="ta_${i}"></textarea>
    <button class="ghost small" onclick="saveEdit(${i})">💾 Save</button>`;
  $("ta_" + i).value = current;
  $("ta_" + i).focus();
}

function saveEdit(i) {
  const p = RUN.payload[i];
  const v = $("ta_" + i).value;
  p.after = v;
  p.edited = true;
  intervene("edit_leaf", p.leaf_id, { after: v });
  log(`Edited ${p.leaf_id} — saved as intervention.`);
  renderResults();
}

function renamePlaceholder(actorId) {
  const v = $("ph_" + actorId).value.trim();
  const a = RUN.actors[actorId];
  a.placeholder = v;
  intervene("rename_placeholder", actorId, { placeholder: v });
  refreshChanges();                      // recomputed from spans, not string surgery
  log(`Renamed: ${a.name} → ${v} (intervention recorded).`);
}

async function removeAnchors() {
  const removed = await autoCleanAnchors();
  op("clean", `${removed} anchor(s) removed manually (content kept).`);
}

/* ── HITL comments + Redo (docs/DESIGN_hitl_comments.md) ──────────────────
 * Three binding gestures feed ONE drawer: 💬 on a dictionary row (actor),
 * 💬 on a change card (leaf), or "💬 Comment on selection" which reads the
 * Word selection (text + paragraph + anchor tag) so a missed surface never
 * has to be typed by hand. Nothing runs until 🔁 Redo — the server arbiter
 * turns each comment into ONE closed operation, invalid output is shown and
 * never executed, and only affected leaves are re-decided. */

/* Owner feedback (HITL run 1): the 💬 button scrolled the pane to the bottom
 * section — the comment must open IN PLACE, as a popover on the element the
 * user is looking at, with its own submit. The bottom section remains the
 * drawer (pending list + selection/free comments). */
function bindToActor(actorId, btn) {
  const a = RUN.actors[actorId];
  openCommentPopover(btn, { actor_id: actorId }, `actor «${a.name}»`);
}

function bindToLeaf(i, btn) {
  const p = RUN.payload[i];
  openCommentPopover(btn, { leaf_id: p.leaf_id },
                     `${p.row ? "row cell " : "text "}${p.leaf_id}`);
}

function openCommentPopover(btn, bind, label) {
  closeCommentPopover();
  PENDING_BIND = { bind, label };
  const pop = document.createElement("div");
  pop.id = "commentPopover";
  pop.innerHTML =
    `<div class="muted" style="margin-bottom:4px">💬 ${esc(label)}</div>` +
    `<textarea id="popText" placeholder="What should change here?"></textarea>` +
    `<div class="actions" style="margin-top:5px">` +
    `<button class="ghost small" onclick="popoverSubmit(true)">🔁 Send &amp; Redo</button>` +
    `<button class="ghost small" onclick="popoverSubmit(false)" title="Queue it — run all queued comments later with one Redo">➕ Queue</button>` +
    `<button class="ghost small" onclick="closeCommentPopover()">✕</button>` +
    `</div>`;
  document.body.appendChild(pop);
  const r = btn ? btn.getBoundingClientRect() : { bottom: 60, left: 20 };
  pop.style.top = (window.scrollY + r.bottom + 6) + "px";
  pop.style.left = Math.max(8, Math.min(
    window.scrollX + r.left - 110,
    document.documentElement.clientWidth - 290)) + "px";
  $("popText").focus();
}

function closeCommentPopover() {
  const pop = $("commentPopover");
  if (pop) pop.remove();
  PENDING_BIND = null;
}

async function popoverSubmit(runNow) {
  const text = ($("popText") ? $("popText").value : "").trim();
  if (!text) { log("Write the comment first."); return; }
  const b = PENDING_BIND;                 // capture before close clears it
  closeCommentPopover();
  await postCommentText(text, b ? b.bind : {}, b ? b.label : "free comment");
  if (runNow) await redoWithComments();
}

/* Case 1 (missed surface): the user selects the text in Word, writes the
 * comment, and this button captures selection + paragraph + anchor tag —
 * the server resolves the exact leaf deterministically. */
async function commentOnSelection() {
  if (!RUN) { log("Run the pipeline first."); return; }
  if (!$("commentText").value.trim()) {
    log("Write the comment first, then click 💬 Comment on selection."); return;
  }
  let bind = null;
  try {
    await Word.run(async (ctx) => {
      const sel = ctx.document.getSelection();
      sel.load("text");
      const para = sel.paragraphs.getFirstOrNullObject();
      para.load("isNullObject,text");
      const cc = sel.parentContentControlOrNullObject;
      cc.load("isNullObject,tag");
      await ctx.sync();
      bind = {
        selected_text: (sel.text || "").trim(),
        paragraph_text: para.isNullObject ? "" : (para.text || "").trim(),
      };
      if (!cc.isNullObject && (cc.tag || "").startsWith("anz:")) bind.anchor = cc.tag;
    });
  } catch (e) { log("ERROR reading the selection: " + fmtErr(e)); return; }
  if (!bind.selected_text) {
    log("Nothing selected — select the target text in the document first."); return;
  }
  const text = $("commentText").value.trim();
  $("commentText").value = "";
  await postCommentText(text, bind, `selection «${bind.selected_text.slice(0, 40)}»`);
}

function submitComment() {
  postCommentText($("commentText").value.trim(), {}, "free comment");
  $("commentText").value = "";
}

async function postCommentText(text, bind, label) {
  if (!RUN) { log("Run the pipeline first."); return; }
  if (!text) { log("Empty comment."); return; }
  try {
    const r = await (await fetch(`${SERVER}/runs/${RUN.run_id}/comments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, bind: bind || {} }),
    })).json();
    if (!r.ok) throw new Error(r.error || "comment rejected");
    r.comment.label = label;
    COMMENTS.push(r.comment);
    renderComments();
    const res = r.comment.resolved || {};
    op("comment", `added (${label}) → ` +
       (res.leaf_id ? `leaf ${res.leaf_id} via ${res.how}` :
        res.actor_id ? `actor ${res.actor_id}` :
        res.note ? `UNRESOLVED: ${res.note}` : "free") +
       ` — ${r.pending} pending.`);
  } catch (e) { log("ERROR adding comment: " + fmtErr(e)); }
}

async function removeComment(id) {
  try {
    const r = await (await fetch(`${SERVER}/runs/${RUN.run_id}/comments/${id}`,
                                 { method: "DELETE" })).json();
    if (!r.ok) throw new Error(r.error || "not found");
    COMMENTS = COMMENTS.filter(c => c.id !== id);
    renderComments();
  } catch (e) { log("ERROR removing comment: " + fmtErr(e)); }
}

/* Outcome of the LAST redo, rendered as visible cards (owner feedback,
 * HITL run 2: "nothing happened when pressing Send & Redo" — the answer
 * lived only in the monospace log). If nothing changed, the report scrolls
 * itself into view so the WHY is unmissable. */
function renderRedoReport(report, updatedCount) {
  const el = $("redoReport");
  if (!report.length) { el.style.display = "none"; return; }
  el.style.display = "block";
  el.innerHTML =
    `<div class="muted" style="margin:4px 0">Last redo — ${updatedCount} card(s) changed:</div>` +
    report.map(e => {
      const color = e.error ? "var(--err)" : e.warning ? "var(--warn)" : "var(--ok)";
      const head = e.error ? "✗" : e.warning ? "⚠" : "✓";
      const what = e.error ? e.error
        : `${e.op}${e.applied ? " — " + e.applied : ""}` +
          (e.mentions_linked != null ? ` — ${e.mentions_linked} new mention(s) linked` : "") +
          (e.warning ? " — " + e.warning : "");
      return `<div class="card" style="border-inline-start:3px solid ${color}">` +
        `<div>${head} “${esc((e.text || "").slice(0, 80))}”</div>` +
        `<div class="muted">${esc(what)}</div></div>`;
    }).join("");
  if (updatedCount === 0) el.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderComments() {
  $("commentCount").textContent = COMMENTS.length;
  const btn = $("redoBtn");
  btn.disabled = !COMMENTS.length;
  btn.textContent = `🔁 Redo with comments (${COMMENTS.length})`;
  $("commentList").innerHTML = COMMENTS.map(c => {
    const res = c.resolved || {};
    const where = res.leaf_id ? `→ ${res.leaf_id}` :
                  res.actor_id ? `→ actor ${res.actor_id}` :
                  res.note ? `⚠ ${res.note}` : "free";
    return `<div class="card"><div>${esc(c.text)}</div>` +
      `<div class="muted">${esc(c.label || "")} ${esc(where)}</div>` +
      `<div class="actions">` +
      `<button class="ghost small" onclick="removeComment('${c.id}')">✕ Remove</button>` +
      `</div></div>`;
  }).join("") || "<div class='muted'>No pending comments — bind one with the 💬 buttons or write a free one.</div>";
}

async function redoWithComments() {
  if (!RUN || !COMMENTS.length) return;
  $("redoBtn").disabled = true;
  $("redoBtn").textContent = "🔁 Redo running…";
  try {
    log(`Redo: processing ${COMMENTS.length} comment(s)…`);
    const r = await (await fetch(`${SERVER}/runs/${RUN.run_id}/redo`,
                                 { method: "POST" })).json();
    if (!r.ok) throw new Error(r.error || "redo failed");
    (r.redo_report || []).forEach(e => {
      if (e.error) op("redo", `✗ "${(e.text || "").slice(0, 50)}" — ${e.error}` +
                             ` (nothing was executed for this comment)`);
      else op("redo", `✓ "${(e.text || "").slice(0, 50)}" → ${e.op}` +
                      (e.applied ? ` (${e.applied})` : "") +
                      (e.mentions_linked != null ? ` — ${e.mentions_linked} new mention(s) linked` : "") +
                      (e.warning ? ` — ⚠ ${e.warning}` : ""));
    });
    RUN = { run_id: RUN.run_id, llm_mode: RUN.llm_mode, ...r.result };
    COMMENTS = [];
    absorbReviewItems();
    indexSpans();
    renderResults();
    renderRedoReport(r.redo_report || [], (r.updated_leaf_ids || []).length);
    op("redo", `${(r.updated_leaf_ids || []).length} card(s) updated by your ` +
       `comments — look for the ↻ badge.`);
  } catch (e) { log("ERROR redo: " + fmtErr(e)); }
  finally {
    $("redoBtn").disabled = !COMMENTS.length;
    renderComments();
  }
}

/* ── diagnostics report — paste it to the developer/assistant to evaluate ── */

async function copyDiagnostics() {
  if (!RUN) { log("Run the pipeline first."); return; }
  let full = null;
  try {
    full = await (await fetch(`${SERVER}/runs/${RUN.run_id}`, { cache: "no-store" })).json();
  } catch { /* fall back to what the pane already holds */ }
  const r = (full && full.result) || RUN;

  const report = {
    diagnostics_version: 3,
    generated_at: new Date().toISOString(),
    ui_version: UI_VERSION,
    run_id: RUN.run_id,
    llm_mode: RUN.llm_mode,
    stages: (full && full.stages) || [],           // now carry per-stage seconds
    events: (full && full.events) || [],           // timestamped background activity
    portrait: r.portrait || {},
    operation_log: OPLOG,                          // clean/anchor/locate/apply outcomes
    metrics: r.metrics || {},
    original_leaves: (r.leaves || []).map(l => ({
      id: l.leaf_id, kind: l.kind, section: l.section,
      row: l.row, anchor: l.anchor, text: l.text,
    })),
    actors: r.actors || {},
    surface_links: (r.links || []).map(l => ({
      leaf: l.leaf_id, actor: l.actor_id, surface: l.surface,
    })),
    classifications: r.classifications || [],
    cascade: r.cascade || [],
    decisions: r.decisions || [],
    payload: (r.payload || []).map(p => ({
      leaf: p.leaf_id, anchor: p.anchor, before: p.before, after: p.after,
    })),
    review_queue: r.review_queue || [],
    warnings: r.warnings || [],
    pending_comments: COMMENTS,
    processed_comments: (full && full.processed_comments) || [],
    redo_report: r.redo_report || [],
    updated_leaf_ids: r.updated_leaf_ids || [],
  };
  const text = "=== ANONYMIZER DIAGNOSTICS ===\n" + JSON.stringify(report, null, 1);

  try {
    await navigator.clipboard.writeText(text);
    log(`Diagnostics copied to clipboard (${(text.length / 1024).toFixed(0)} KB) — paste it to the assistant.`);
  } catch {
    // clipboard API blocked in this webview → show a selectable box
    const box = $("diagBox");
    box.style.display = "block";
    box.value = text;
    box.focus();
    box.select();
    try {
      document.execCommand("copy");
      log("Diagnostics copied (fallback). The box below holds the full report.");
    } catch {
      log("Clipboard blocked — select the box below manually (Ctrl+A, Ctrl+C).");
    }
  }
}

/* ── util ─────────────────────────────────────────────────────────────────── */

function intervene(type, target, payload) {
  if (!RUN) return;
  fetch(`${SERVER}/runs/${RUN.run_id}/interventions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, target, payload, note: "" }),
  }).catch(() => {});
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* Rich Office.js error formatting — GeneralException alone is useless;
 * debugInfo carries the failing statement and surrounding trace. */
function fmtErr(e) {
  if (e && e.debugInfo) {
    const d = e.debugInfo;
    return `${e.code || e.name}: ${e.message}` +
           (d.errorLocation ? ` @ ${d.errorLocation}` : "") +
           (d.fullStatements ? ` | near: ${(d.fullStatements || []).slice(-1)[0]}` : "");
  }
  return (e && (e.message || e.code)) || String(e);
}
