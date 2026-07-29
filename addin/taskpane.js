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

const UI_VERSION = "0.7.0";        // bump when the pane changes — shown in the header
let RUN = null;                    // completed run result
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

/* Auto-clean: remove ALL anz anchors left by earlier runs BEFORE the agent
 * receives the document (run-5 owner rule: "clean before the agent gets it").
 * Content is kept; only the control wrappers go. This removes the whole
 * duplicate-tag failure class — every run anchors fresh from C_00001. */
async function autoCleanAnchors() {
  let removed = 0;
  await Word.run(async (ctx) => {
    const ccs = ctx.document.contentControls;
    ccs.load("items/tag");
    await ctx.sync();
    const stale = ccs.items.filter(c => (c.tag || "").startsWith("anz:"));
    stale.forEach(c => c.delete(true));   // true = keep the content
    removed = stale.length;
    await ctx.sync();
  });
  anchorSeq = 0;
  return removed;
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
      const removed = await autoCleanAnchors();
      op("clean", removed
        ? `${removed} stale anchor(s) from earlier runs removed automatically — document is clean.`
        : "no stale anchors — document already clean.");
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
async function selectByAnchorOrText(anchor, text, nth = 0) {
  if (anchor) {
    try { await withCc(anchor, async (_c, cc) => cc.select()); return "anchor " + anchor; }
    catch (e) {
      op("locate", `anchor ${anchor} lookup failed (${fmtErr(e)}) — falling back to text search`);
    }
  }
  if (!text) throw new Error("nothing to locate");
  let how = "";
  await Word.run(async (ctx) => {
    const found = ctx.document.body.search(text.slice(0, 120), { matchCase: true });
    found.load("items");
    await ctx.sync();
    op("locate", `text search "${text.slice(0, 60)}" → ${found.items.length} match(es)`);
    if (!found.items.length) throw new Error("not found by anchor or text (floating text box?)");
    found.items[Math.min(nth, found.items.length - 1)].select();
    how = "text search";
    await ctx.sync();
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

function recomputeAfter(p) {
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
        `<button class="ghost small" onclick="toggleIgnore('${a.actor_id}')">${off ? "↩ Restore" : "🚫 Ignore"}</button>` +
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
  // apply together, never as scattered independent cards.
  const pl = (RUN.payload || []).filter(p => !p.empty);
  $("changeCount").textContent = pl.length;
  const rows = {};                       // row key -> [payload indices]
  const singles = [];
  pl.forEach(p => {
    const i = RUN.payload.indexOf(p);
    if (p.row) (rows[p.row] = rows[p.row] || []).push(i);
    else singles.push(i);
  });

  const cellHtml = (i) => {
    const p = RUN.payload[i];
    return `<tr id="cell_${i}"><td class="muted">${esc(p.column || "")}</td>` +
      `<td><div class="before">${esc(p.before)}</div>` +
      `<div class="after" id="after_${i}">${esc(p.after)}</div></td>` +
      `<td><button class="ghost small" onclick="editItem(${i})">✎</button></td></tr>`;
  };

  const rowCards = Object.entries(rows).map(([rk, idxs]) =>
    `<div class="card" id="row_${esc(rk)}">` +
    `<span class="tag">table row ${esc(rk)}</span>` +
    `<table style="border:0">${idxs.map(cellHtml).join("")}</table>` +
    `<div class="actions">` +
    `<button class="ghost small" onclick='applyRow(${JSON.stringify(idxs)})'>✓ Apply row</button>` +
    `<button class="ghost small" onclick="goTo(${idxs[0]})">Locate</button>` +
    `<button class="ghost small" onclick='rejectRow(${JSON.stringify(idxs)}, "${esc(rk)}")'>✗ Reject row</button>` +
    `</div></div>`);

  const singleCards = singles.map(i => {
    const p = RUN.payload[i];
    return `<div class="card" id="chg_${i}">` +
    `<div class="before">${esc(p.before)}</div>` +
    `<div class="after" id="after_${i}">${esc(p.after)}</div>` +
    `<div class="actions">` +
    `<button class="ghost small" onclick="applyOne(${i})">✓ Apply</button>` +
    `<button class="ghost small" onclick="editItem(${i})">✎ Edit</button>` +
    `<button class="ghost small" onclick="goTo(${i})">Locate</button>` +
    `<button class="ghost small" onclick="reject(${i})">✗ Reject</button>` +
    `<span class="anchor" style="margin-inline-start:auto">${esc(p.anchor || p.leaf_id)}</span>` +
    `</div></div>`;
  });

  $("changes").innerHTML = (singleCards.join("") + rowCards.join("")) ||
    "<div class='muted'>No changes proposed.</div>";
  show("secChanges");
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

/* Text-search fallback for leaves without an anchor (Word refuses to wrap
 * built-in cover/title controls). Replaces ONLY on an exact, UNIQUE match —
 * zero or multiple matches abort, because guessing would corrupt text. */
async function replaceByUniqueText(p) {
  const needle = p.before || "";
  if (!needle) throw new Error("empty source text");
  if (needle.length > 250)
    throw new Error(`text too long for the search fallback (${needle.length} chars)`);
  await Word.run(async (ctx) => {
    try { ctx.document.changeTrackingMode = "TrackAll"; } catch { /* WordApi<1.4 */ }
    const found = ctx.document.body.search(needle, { matchCase: true });
    found.load("items");
    await ctx.sync();
    if (found.items.length === 0) throw new Error("text not found in body");
    if (found.items.length > 1)
      throw new Error(`ambiguous — ${found.items.length} matches, skipped for safety`);
    found.items[0].insertText(p.after, "Replace");
    await ctx.sync();
  });
  return "unique text match";
}

/* Every apply is LOGGED with its outcome — run-5 finding: replacements were
 * failing with no trace. Returns true on success so applyAll can count. */
async function applyOne(i) {
  const p = RUN.payload[i];
  const id = p.leaf_id + (p.anchor ? ` (${p.anchor})` : " (no anchor)");
  let via = null;
  const errs = [];
  if (p.anchor) {
    try {
      await withCc(p.anchor, async (_ctx, cc) => cc.insertText(p.after, "Replace"));
      via = "anchor";
    } catch (e) { errs.push("anchor: " + fmtErr(e)); }
  } else {
    errs.push("no anchor (Word refused to wrap this paragraph — cover/title control)");
  }
  if (!via) {
    try { via = await replaceByUniqueText(p); }
    catch (e) { errs.push("text fallback: " + fmtErr(e)); }
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
  let ok = 0;
  const failed = [];
  for (let i = 0; i < (RUN.payload || []).length; i++) {
    const p = RUN.payload[i];
    if (p.empty || p.rejected || p.applied) continue;
    (await applyOne(i)) ? ok++ : failed.push(p.leaf_id);
  }
  op("apply", `SUMMARY: ${ok} applied, ${failed.length} failed` +
      (failed.length ? ` — ${failed.join(", ")}` : "") +
      ". Review them in Word's Review tab (tracked changes).");
}

async function goTo(i) {
  const p = RUN.payload[i];
  try { await selectByAnchorOrText(p.anchor, p.before); }
  catch (e) { log("ERROR locating: " + fmtErr(e)); }
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

/* ── diagnostics report — paste it to the developer/assistant to evaluate ── */

async function copyDiagnostics() {
  if (!RUN) { log("Run the pipeline first."); return; }
  let full = null;
  try {
    full = await (await fetch(`${SERVER}/runs/${RUN.run_id}`, { cache: "no-store" })).json();
  } catch { /* fall back to what the pane already holds */ }
  const r = (full && full.result) || RUN;

  const report = {
    diagnostics_version: 2,
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
