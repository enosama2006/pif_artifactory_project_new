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

let RUN = null;                    // completed run result
let SERVER = "http://localhost:8080";
const STAGE_LABELS = {
  ingest: "Ingest — leaf inventory",
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

Office.onReady(() => {
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

async function anchorDocument() {
  anchorSeq = 0;
  let total = 0;
  await Word.run(async (ctx) => {
    const paras = ctx.document.body.paragraphs;
    paras.load("items");
    await ctx.sync();
    total = paras.items.length;
  });

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
    let cls = "step", ico = "";
    if (done.has(name)) { cls += " done"; ico = "✓"; }
    else if (rec?.current_stage === name) { cls += " current"; }
    else if (rec?.status === "error" && rec?.error?.startsWith(name)) { cls += " error"; ico = "!"; }
    return `<div class="${cls}"><span class="ico">${ico}</span>` +
           `<span>${STAGE_LABELS[name] || name}</span>` +
           `<span class="msg" title="${esc(msgs[name] || "")}">${esc(msgs[name] || "")}</span></div>`;
  }).join("");
}

async function runPipeline() {
  SERVER = $("serverUrl").value.replace(/\/+$/, "");
  $("runBtn").disabled = true;
  ["secDict", "secReview", "secChanges"].forEach(s => show(s, false));
  show("metrics", false);
  try {
    log("Anchoring paragraphs with content controls…");
    let a = { anchored: 0, skipped: 0, failed: 0, total: 0 };
    try {
      a = await anchorDocument();
      log(`Anchored ${a.anchored}/${a.total} paragraph(s)` +
          (a.skipped ? `, ${a.skipped} skipped (empty/already tagged)` : "") +
          (a.failed ? `, ${a.failed} refused by Word` : "") + ".");
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
      actors.map(a =>
        `<tr><td>${esc(a.name)}</td><td>${esc((a.roles || []).join(", "))}</td>` +
        `<td><input class="ph" id="ph_${a.actor_id}" value="${esc(a.placeholder)}"></td>` +
        `<td><button class="ghost small" onclick="renamePlaceholder('${a.actor_id}')">Save</button></td></tr>`
      ).join("") + "</table>"
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

  const pl = RUN.payload || [];
  $("changeCount").textContent = pl.length;
  $("changes").innerHTML = pl.map((p, i) =>
    `<div class="card" id="chg_${i}">` +
    `<div class="before">${esc(p.before)}</div>` +
    `<div class="after">${esc(p.after)}</div>` +
    `<div class="actions">` +
    `<button class="ghost small" onclick="applyOne(${i})">✓ Apply</button>` +
    `<button class="ghost small" onclick="goTo(${i})">Locate</button>` +
    `<button class="ghost small" onclick="reject(${i})">✗ Reject</button>` +
    `<span class="anchor" style="margin-inline-start:auto">${esc(p.anchor || p.leaf_id)}</span>` +
    `</div></div>`).join("") ||
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

async function applyOne(i) {
  const p = RUN.payload[i];
  if (!p.anchor) {
    log(`No anchor for ${p.leaf_id} (page header/footer?) — apply manually: ${p.after}`);
    return;
  }
  try {
    await withCc(p.anchor, async (_ctx, cc) => cc.insertText(p.after, "Replace"));
    $("chg_" + i).classList.add("applied");
    intervene("accept_leaf", p.leaf_id, {});
  } catch (e) { log("ERROR: " + e.message); }
}

async function applyAll() {
  for (let i = 0; i < (RUN.payload || []).length; i++) {
    const el = $("chg_" + i);
    if (el && !el.classList.contains("applied") && !el.classList.contains("rejected"))
      await applyOne(i);
  }
  log("All changes applied as tracked changes — review them in Word's Review tab.");
}

async function goTo(i) {
  const p = RUN.payload[i];
  if (!p.anchor) return;
  try { await withCc(p.anchor, async (_c, cc) => cc.select()); }
  catch (e) { log("ERROR: " + e.message); }
}

function reject(i) {
  const p = RUN.payload[i];
  $("chg_" + i).classList.add("rejected");
  intervene("reject_leaf", p.leaf_id, {});
}

function renamePlaceholder(actorId) {
  const v = $("ph_" + actorId).value.trim();
  const a = RUN.actors[actorId];
  const old = a.placeholder;
  a.placeholder = v;
  (RUN.payload || []).forEach(p => { p.after = p.after.split(old).join(v); });
  intervene("rename_placeholder", actorId, { placeholder: v });
  renderResults();
  log(`Renamed: ${a.name} → ${v} (intervention recorded).`);
}

async function removeAnchors() {
  await Word.run(async (ctx) => {
    const ccs = ctx.document.contentControls;
    ccs.load("items/tag");
    await ctx.sync();
    ccs.items.forEach(c => { if ((c.tag || "").startsWith("anz:")) c.delete(true); });
    await ctx.sync();
  });
  log("Anchors removed (content kept).");
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
