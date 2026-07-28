/* Anonymizer taskpane — plain Office.js, no build step.
 *
 * Flow (docs/DESIGN_repo_and_ux.md):
 *  1. ANCHOR: wrap every paragraph in a content control tagged anz:C_NNNNN —
 *     the agent reads the tags from the OOXML, so applying is by tag lookup,
 *     never text search.
 *  2. UPLOAD: getOoxml() → POST {server}/runs → full result.
 *  3. REVIEW: dictionary first (rename = one fix for all mentions), then the
 *     changes list; every user action → POST /interventions.
 *  4. APPLY: tracked changes via the anchors; Word's native accept/reject
 *     remains the final safety net.
 */
/* global Office, Word, fetch, document */

let RUN = null;          // last /runs response
let SERVER = "http://localhost:8080";

const $ = (id) => document.getElementById(id);
const log = (msg, append = true) => {
  const el = $("status");
  el.textContent = append ? (el.textContent + "\n" + msg) : msg;
  el.scrollTop = el.scrollHeight;
};

Office.onReady(() => log("جاهز. تأكد أن الوكيل يعمل ثم اضغط «ابدأ التجريد».", false));

async function checkHealth() {
  SERVER = $("serverUrl").value.replace(/\/+$/, "");
  try {
    const r = await (await fetch(SERVER + "/health")).json();
    log(`✅ الوكيل يعمل — وضع النموذج: ${r.llm_mode === "groq" ? "Groq حقيقي" : "تجريبي (بلا مفتاح)"}`, false);
  } catch (e) {
    log("⛔ تعذر الوصول للوكيل على " + SERVER + "\nشغّله بـ: uvicorn app.api.routes:app --port 8080", false);
  }
}

/* ── 1. anchoring pass ────────────────────────────────────────────────────── */

async function anchorDocument() {
  let count = 0;
  await Word.run(async (ctx) => {
    const paras = ctx.document.body.paragraphs;
    paras.load("items");
    await ctx.sync();
    for (let i = 0; i < paras.items.length; i++) {
      const p = paras.items[i];
      if (p.contentControls) {
        p.contentControls.load("items/tag");
      }
    }
    await ctx.sync();
    for (let i = 0; i < paras.items.length; i++) {
      const p = paras.items[i];
      const already = p.contentControls.items.some(c => (c.tag || "").startsWith("anz:"));
      if (already) continue;
      const cc = p.insertContentControl();
      cc.tag = "anz:C_" + String(i + 1).padStart(5, "0");
      cc.appearance = "Hidden";
      count++;
    }
    await ctx.sync();
  });
  return count;
}

/* ── 2. run ───────────────────────────────────────────────────────────────── */

async function runPipeline() {
  SERVER = $("serverUrl").value.replace(/\/+$/, "");
  $("runBtn").disabled = true;
  try {
    log("① ترسيخ الفقرات بوسوم content controls …", false);
    const anchored = await anchorDocument();
    log(`   تم ترسيخ ${anchored} فقرة`);

    log("② استخراج OOXML وإرساله للوكيل …");
    let ooxml = "";
    await Word.run(async (ctx) => {
      const x = ctx.document.body.getOoxml();
      await ctx.sync();
      ooxml = x.value;
    });

    log("③ الوكيل يعالج (قد يستغرق دقيقة لمستند كبير) …");
    const resp = await fetch(SERVER + "/runs", {
      method: "POST",
      headers: { "Content-Type": "application/xml" },
      body: ooxml,
    });
    RUN = await resp.json();
    if (!RUN.ok) throw new Error(RUN.error || "فشل التشغيل");

    const m = RUN.metrics;
    log(`✅ اكتمل — أوراق: ${m.leaves} | تغطية: ${m.coverage} | استبدالات: ${m.rewrites}` +
        ` | مراجعة: ${m.review} | خسائر صامتة: ${m.silent_losses}` +
        `\n   وضع النموذج: ${RUN.llm_mode === "groq" ? "Groq" : "تجريبي — ضع GROQ_API_KEY في agent/.env"}`);
    RUN.stages.forEach(s => log(`   [${s.stage}] ${s.message}`));
    render();
  } catch (e) {
    log("⛔ " + (e.message || e));
  } finally {
    $("runBtn").disabled = false;
  }
}

/* ── 3. render dictionary / review / changes ─────────────────────────────── */

function render() {
  $("results").style.display = "block";
  const actors = Object.values(RUN.actors || {});
  $("actorCount").textContent = actors.length;
  $("dict").innerHTML = actors.length
    ? "<table><tr><th>الاسم</th><th>الدور</th><th>المسمى البديل</th><th></th></tr>" +
      actors.map(a =>
        `<tr><td>${esc(a.name)}</td><td>${esc((a.roles || []).join("، "))}</td>` +
        `<td><input class="ph" id="ph_${a.actor_id}" value="${esc(a.placeholder)}"></td>` +
        `<td><button class="mini" onclick="renamePlaceholder('${a.actor_id}')">حفظ</button></td></tr>`
      ).join("") + "</table>"
    : "<div class='muted'>لا هويات (وضع تجريبي؟ ضع مفتاح Groq وأعد التشغيل)</div>";

  const rq = RUN.review_queue || [];
  $("reviewCount").textContent = rq.length;
  $("review").innerHTML = rq.length
    ? rq.map(r =>
        `<div class="card review"><div>${esc(r.text)}</div>` +
        `<div class="muted">السبب: ${esc(r.reason)}</div></div>`).join("")
    : "<div class='muted'>لا شيء يحتاج مراجعة 🎉</div>";

  const pl = RUN.payload || [];
  $("changeCount").textContent = pl.length;
  $("changes").innerHTML = pl.map((p, i) =>
    `<div class="card" id="chg_${i}">` +
    `<span class="tag">${esc(p.anchor || p.leaf_id)}</span>` +
    `<div class="before">${esc(p.before)}</div>` +
    `<div class="after">${esc(p.after)}</div>` +
    `<button class="mini" onclick="applyOne(${i})">✓ طبّق</button>` +
    `<button class="mini" onclick="goTo(${i})">👁 اذهب</button>` +
    `<button class="mini" onclick="reject(${i})">✗ ارفض</button>` +
    `</div>`).join("");
}

/* ── 4. apply via anchors as tracked changes ─────────────────────────────── */

async function withCc(anchor, fn) {
  await Word.run(async (ctx) => {
    ctx.document.changeTrackingMode = "TrackAll";
    const ccs = ctx.document.contentControls.getByTag(anchor);
    ccs.load("items");
    await ctx.sync();
    if (!ccs.items.length) throw new Error("لم يُعثر على الوسم " + anchor);
    await fn(ctx, ccs.items[0]);
    await ctx.sync();
  });
}

async function applyOne(i) {
  const p = RUN.payload[i];
  if (!p.anchor) { log("⛔ لا وسم لهذه الورقة (ترويسة صفحة؟) — طبّقها يدويًا: " + p.after); return; }
  try {
    await withCc(p.anchor, async (_ctx, cc) => cc.insertText(p.after, "Replace"));
    $("chg_" + i).style.opacity = 0.45;
    intervene("accept_leaf", p.leaf_id, {});
  } catch (e) { log("⛔ " + e.message); }
}

async function applyAll() {
  for (let i = 0; i < (RUN.payload || []).length; i++) {
    const el = $("chg_" + i);
    if (el && el.style.opacity !== "0.45") await applyOne(i);
  }
  log("✅ طُبقت كل التغييرات كتغييرات متعقبة — راجعها من تبويب «مراجعة» في وورد");
}

async function goTo(i) {
  const p = RUN.payload[i];
  if (!p.anchor) return;
  try { await withCc(p.anchor, async (_c, cc) => cc.select()); } catch (e) { log("⛔ " + e.message); }
}

function reject(i) {
  const p = RUN.payload[i];
  $("chg_" + i).style.opacity = 0.3;
  intervene("reject_leaf", p.leaf_id, {});
}

function renamePlaceholder(actorId) {
  const v = $("ph_" + actorId).value.trim();
  intervene("rename_placeholder", actorId, { placeholder: v });
  // local re-render: apply the rename to pending payload previews
  const a = RUN.actors[actorId];
  const old = a.placeholder;
  a.placeholder = v;
  (RUN.payload || []).forEach(p => { p.after = p.after.split(old).join(v); });
  render();
  log(`✏️ ${esc(a.name)} ← ${v} (سُجّل التدخل)`);
}

async function removeAnchors() {
  await Word.run(async (ctx) => {
    const ccs = ctx.document.contentControls;
    ccs.load("items/tag");
    await ctx.sync();
    ccs.items.forEach(c => { if ((c.tag || "").startsWith("anz:")) c.delete(true); });
    await ctx.sync();
  });
  log("🧹 أُزيلت وسوم الترسيخ (بقي المحتوى)");
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
