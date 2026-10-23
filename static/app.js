const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rs = (n) => "Rs " + Math.round(n).toLocaleString("en-PK");
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};

let SECTIONS = {};
let pickedSection = "";
let mistriSection = "";
let lastDiagnosis = null;
let myJobIds = store.get("rozgar.myJobs", []);

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || data.detail || `Request failed (${r.status})`);
  return data;
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
const imgSrc = (p) => (p ? `data:${p.mime};base64,${p.b64}` : "");

// Phone photos are 3-8 MB; Gemma only needs ~1024 px, so shrink before upload.
async function shrink(file, max = 1024) {
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * scale);
    c.height = Math.round(bmp.height * scale);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.85));
    return blob ? new File([blob], "photo.jpg", { type: "image/jpeg" }) : file;
  } catch {
    return file;
  }
}

// ---------- roles ----------
document.querySelectorAll(".role").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".role").forEach((x) => x.classList.toggle("active", x === b));
    $("#customer").classList.toggle("hidden", b.dataset.role !== "customer");
    $("#mistri").classList.toggle("hidden", b.dataset.role !== "mistri");
    store.set("rozgar.role", b.dataset.role);
    refresh();
  })
);

function tilesHTML(active, allLabel) {
  const all = allLabel ? `<button class="tile ${active === "" ? "active" : ""}" data-s=""><span class="e">🧰</span><span class="n">${allLabel}</span></button>` : "";
  return all + Object.entries(SECTIONS).map(([id, s]) =>
    `<button class="tile ${active === id ? "active" : ""}" data-s="${id}"><span class="e">${s.emoji}</span><span class="n">${esc(s.name.split(" (")[0])}</span><span class="w">${esc(s.worker)}</span></button>`
  ).join("");
}

function renderTiles() {
  $("#tiles").innerHTML = tilesHTML(pickedSection);
  $("#tiles").querySelectorAll(".tile").forEach((t) => t.addEventListener("click", () => {
    pickedSection = pickedSection === t.dataset.s ? "" : t.dataset.s;
    renderTiles();
  }));
  $("#mTiles").innerHTML = tilesHTML(mistriSection, "All jobs");
  $("#mTiles").querySelectorAll(".tile").forEach((t) => t.addEventListener("click", () => {
    mistriSection = t.dataset.s;
    renderTiles();
    loadOpenJobs();
  }));
}

// ---------- customer: diagnose ----------
$("#photo").addEventListener("change", () => {
  const f = $("#photo").files[0];
  if (!f) return;
  $("#preview").src = URL.createObjectURL(f);
  $("#preview").classList.remove("hidden");
});

const STEPS = ["Router agent: choosing the right section", "Specialist agent: diagnosing the problem", "Pricing tool: fair price from rates.json", "Safety guard: checking for danger"];
let progTimer;
function showProgress(on) {
  const el = $("#progress");
  clearInterval(progTimer);
  if (!on) { el.classList.add("hidden"); return; }
  let i = 0;
  const draw = () => { el.innerHTML = STEPS.map((s, j) => `<div class="step ${j < i ? "done" : j === i ? "run" : ""}">${s}</div>`).join(""); };
  el.classList.remove("hidden");
  draw();
  progTimer = setInterval(() => { if (i < 1) { i++; draw(); } }, 9000);
}

async function diagnose(answer = "") {
  const fd = new FormData();
  const f = $("#photo").files[0];
  if (f) fd.append("photo", await shrink(f));
  fd.append("description", $("#desc").value);
  fd.append("section", pickedSection);
  if (answer) fd.append("answer", answer);
  if (!f && !$("#desc").value.trim()) { alert("Add a photo or describe the problem."); return; }

  const btn = $("#diagnoseBtn");
  btn.disabled = true;
  btn.textContent = "Gemma 4 agents working…";
  $("#clarify").classList.add("hidden");
  $("#result").classList.add("hidden");
  showProgress(true);
  try {
    const d = await api("/api/diagnose", { method: "POST", body: fd });
    if (d.status === "clarify") {
      $("#clarify").innerHTML = `<b>Gemma needs one detail:</b> ${esc(d.question)}<input id="clarifyAns" placeholder="Your answer"><button class="secondary" id="clarifyBtn">Continue</button>`;
      $("#clarify").classList.remove("hidden");
      $("#clarifyBtn").onclick = () => diagnose($("#clarifyAns").value);
      return;
    }
    lastDiagnosis = d;
    renderResult(d);
  } catch (e) {
    $("#result").innerHTML = `<div class="card danger"><b>Could not diagnose.</b> ${esc(e.message)}<br><span class="small">Gemma may be busy. Wait a few seconds and try again.</span></div>`;
    $("#result").classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = "🔍 Diagnose with Gemma 4";
    showProgress(false);
  }
}
$("#diagnoseBtn").addEventListener("click", () => diagnose());

function safetyHTML(s) {
  if (s.level === "none") return "";
  const cls = s.level === "danger" ? "danger" : "warn";
  const title = s.level === "danger" ? "🚨 DANGER: make it safe first" : "⚠️ Be careful";
  return `<div class="card ${cls}"><b>${title}</b><br>${esc(s.advice)}${s.rule_reasons.map((r) => `<br>• ${esc(r)}`).join("")}<br><b>Helpline:</b> ${s.helplines.map(esc).join(" · ")}</div>`;
}

function priceTable(p) {
  const rows = p.materials.map((m) => `<tr><td>${esc(m.item)} × ${m.qty}</td><td>${rs(m.total_pkr)}</td></tr>`).join("");
  return `<table>${rows}
    <tr><td>Labour ${p.labour_hours} h × ${rs(p.labour_per_hour)}</td><td>${rs(p.labour_total)}</td></tr>
    <tr><td>Visit fee</td><td>${rs(p.visit_fee)}</td></tr>
    <tr><td><b>Base estimate</b></td><td><b>${rs(p.base)}</b></td></tr></table>`;
}

function traceHTML(trace) {
  return `<details><summary>🧠 Agent trace (${trace.length} steps)</summary><div class="trace">${trace.map((t) =>
    `<div class="t"><b>${esc(t.agent)}</b> <span class="muted">· ${esc(t.model)} · ${(t.ms / 1000).toFixed(1)}s</span><pre>${esc(JSON.stringify(t.output, null, 2))}</pre></div>`
  ).join("")}</div></details>`;
}

function offerCheck(v, p) {
  if (v > p.fair_max * 1.25) return `<span class="check high">⚠️ Above fair price</span>`;
  if (v < p.fair_min * 0.75) return `<span class="check low">⚠️ Unfair to worker: too low</span>`;
  return `<span class="check fair">✅ Fair offer</span>`;
}

function renderResult(d) {
  const s = d.section_info, p = d.price;
  const danger = d.safety.level === "danger";
  const start = Math.round((p.fair_min + p.fair_max) / 2 / 50) * 50;
  $("#result").innerHTML = `
    ${safetyHTML(d.safety)}
    <div class="card">
      <span class="badge">${s.emoji} ${esc(s.name)} → ${esc(s.worker)} · ${esc(s.worker_urdu)}</span>
      <h3 style="margin-top:10px">${esc(d.diagnosis)}</h3>
      <p class="muted">${esc(d.likely_cause)} · Difficulty: ${esc(d.difficulty)}</p>
      <div class="muted small">Fair price</div>
      <div class="price">${rs(p.fair_min)} – ${rs(p.fair_max)}</div>
      <details><summary>How this price was calculated</summary>${priceTable(p)}<p class="small muted">Fair range = base ± 20%. Rates come from the open Rozgar skill (rates.json).</p></details>
      <div class="card"><div class="muted small">Job card for the ${esc(s.worker)}</div><div class="urdu">${esc(d.job_card_urdu)}</div></div>
      <h3>3. Your offer (like InDrive)</h3>
      <input type="range" id="offer" min="${Math.round(p.fair_min * 0.5)}" max="${Math.round(p.fair_max * 2)}" step="50" value="${start}">
      <div class="row" style="justify-content:space-between"><b id="offerVal">${rs(start)}</b><span id="offerChk">${offerCheck(start, p)}</span></div>
      <button class="primary" id="postBtn">${danger ? "⚠️ I have made it safe. Post job" : "📢 Post job to " + esc(s.worker) + "s"}</button>
      ${traceHTML(d.trace)}
    </div>`;
  $("#result").classList.remove("hidden");
  $("#offer").addEventListener("input", (e) => {
    const v = +e.target.value;
    $("#offerVal").textContent = rs(v);
    $("#offerChk").innerHTML = offerCheck(v, p);
  });
  $("#postBtn").addEventListener("click", postJob);
  $("#result").scrollIntoView({ behavior: "smooth" });
}

async function postJob() {
  if (lastDiagnosis.safety.level === "danger" && !confirm("This job was flagged as dangerous. Have you made the area safe (main switch / gas valve off)?")) return;
  try {
    const job = await postJSON("/api/jobs", { diagnosis: lastDiagnosis, offer: +$("#offer").value, area: $("#area").value });
    myJobIds.unshift(job.id);
    store.set("rozgar.myJobs", myJobIds);
    $("#result").innerHTML = `<div class="card ok">✅ Job posted to ${esc(job.section_info.worker)}s. Bids will appear below.</div>`;
    $("#photo").value = ""; $("#desc").value = ""; $("#preview").classList.add("hidden");
    lastDiagnosis = null;
    loadMyJobs();
  } catch (e) { alert(e.message); }
}

// ---------- customer: my jobs ----------
function statusBadge(j) {
  return { open: "🟢 Open for bids", in_progress: "🔧 In progress", verified: "✅ Verified: release payment" }[j.status];
}

function verificationHTML(j) {
  const v = j.verification;
  if (!v) return "";
  const cls = v.fixed === "yes" ? "ok" : "danger";
  const label = v.fixed === "yes" ? "✅ Gemma verified the repair" : v.fixed === "no" ? "❌ Not fixed: ask the mistri to check" : "❓ Unclear: ask for a clearer photo";
  return `<div class="card ${cls}"><b>${label}</b> <span class="small">(${Math.round(v.confidence * 100)}% confident)</span><br>${esc(v.evidence)}
    <div class="row" style="margin-top:8px">${j.photo ? `<div><div class="small">Before</div><img class="thumb" src="${imgSrc(j.photo)}"></div>` : ""}<div><div class="small">After</div><img class="thumb" src="${imgSrc(v.after_photo)}"></div></div></div>`;
}

async function loadMyJobs() {
  if (!myJobIds.length) return;
  const jobs = (await Promise.all(myJobIds.map((id) => api(`/api/jobs/${id}`).catch(() => null)))).filter(Boolean);
  if (!jobs.length) { $("#myJobs").innerHTML = `<p class="muted">No jobs yet.</p>`; return; }
  const html = jobs.map((j) => {
    const bids = j.bids.map((b) => `<div class="bid"><div><b>${esc(b.worker)}</b> offers <b>${rs(b.amount)}</b>${b.note ? `<br><span class="small muted">${esc(b.note)}</span>` : ""}<br><span class="check ${b.check.status}">${b.check.label}</span></div>
      ${j.status === "open" ? `<button class="secondary" data-accept="${j.id}:${b.id}">Accept</button>` : j.accepted?.id === b.id ? `<span class="badge">Accepted</span>` : ""}</div>`).join("");
    return `<div class="card"><div class="row">${j.photo ? `<img class="thumb" src="${imgSrc(j.photo)}">` : ""}<div>
      <span class="badge">${j.section_info.emoji} ${esc(j.section_info.worker)}</span> <span class="small">${statusBadge(j)}</span>
      <h3 style="margin-top:6px">${esc(j.diagnosis)}</h3>
      <div class="small muted">Your offer ${rs(j.offer)} · Fair ${rs(j.price.fair_min)} – ${rs(j.price.fair_max)}</div></div></div>
      ${bids || (j.status === "open" ? `<p class="muted small">Waiting for bids…</p>` : "")}
      ${verificationHTML(j)}
      ${traceHTML(j.trace)}</div>`;
  }).join("");
  if (html === lastMyHTML) return;
  lastMyHTML = html;
  $("#myJobs").innerHTML = html;
  $("#myJobs").querySelectorAll("[data-accept]").forEach((b) => b.addEventListener("click", async () => {
    const [jobId, bidId] = b.dataset.accept.split(":");
    await postJSON(`/api/jobs/${jobId}/accept`, { bid_id: bidId }).catch((e) => alert(e.message));
    loadMyJobs();
  }));
}

// ---------- mistri ----------
$("#workerName").value = store.get("rozgar.worker", "");
$("#workerName").addEventListener("change", () => store.set("rozgar.worker", $("#workerName").value));

let typing = false;
let lastMyHTML = "", lastOpenHTML = "";
async function loadOpenJobs() {
  if (typing) return;
  const jobs = await api(`/api/jobs?section=${mistriSection}`).catch(() => []);
  const me = $("#workerName").value.trim();
  if (!jobs.length) { $("#openJobs").innerHTML = `<p class="muted">No jobs in this section yet.</p>`; return; }
  const html = jobs.map((j) => {
    const mine = j.accepted && j.accepted.worker === me;
    let action = "";
    if (j.status === "open") {
      action = `<div class="row"><input type="number" inputmode="numeric" placeholder="Your price (Rs)" data-bid="${j.id}" style="flex:1"><button class="secondary" data-send="${j.id}">Send bid</button></div><div class="small" data-bidchk="${j.id}"></div>`;
    } else if (j.status === "in_progress" && mine) {
      action = `<label class="upload"><input type="file" accept="image/*" capture="environment" data-after="${j.id}"><span>📷 Job done? Upload the AFTER photo for Gemma to verify</span></label>`;
    } else {
      action = `<p class="small">${statusBadge(j)}${j.accepted ? ` · ${esc(j.accepted.worker)} at ${rs(j.accepted.amount)}` : ""}</p>`;
    }
    return `<div class="card"><div class="row">${j.photo ? `<img class="thumb" src="${imgSrc(j.photo)}">` : ""}<div>
      <span class="badge">${j.section_info.emoji} ${esc(j.section_info.name)}</span>${j.safety.level === "danger" ? ` <span class="badge" style="color:var(--danger)">🚨 Danger</span>` : ""}
      <h3 style="margin-top:6px">${esc(j.problem_summary)}</h3>
      <div class="small muted">${esc(j.area || "Faisalabad")} · Customer offers <b>${rs(j.offer)}</b> · Fair ${rs(j.price.fair_min)} – ${rs(j.price.fair_max)}</div></div></div>
      <div class="urdu">${esc(j.job_card_urdu)}</div>
      <div class="small muted">Bring: ${j.price.materials.map((m) => esc(m.item)).join(", ") || "basic tools"}</div>
      ${action}${verificationHTML(j)}</div>`;
  }).join("");
  if (html === lastOpenHTML) return;
  lastOpenHTML = html;
  $("#openJobs").innerHTML = html;

  $("#openJobs").querySelectorAll("[data-bid]").forEach((inp) => {
    const j = jobs.find((x) => x.id === inp.dataset.bid);
    inp.addEventListener("focus", () => (typing = true));
    inp.addEventListener("blur", () => setTimeout(() => (typing = false), 300));
    inp.addEventListener("input", () => {
      const v = +inp.value;
      $(`[data-bidchk="${j.id}"]`).innerHTML = v ? offerCheck(v, j.price) : "";
    });
  });
  $("#openJobs").querySelectorAll("[data-send]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.send;
    const amount = +$(`[data-bid="${id}"]`).value;
    const worker = $("#workerName").value.trim();
    if (!worker) { alert("Enter your name first."); return; }
    if (!amount) { alert("Enter your price."); return; }
    typing = false;
    try {
      const bid = await postJSON(`/api/jobs/${id}/bids`, { worker, amount });
      alert(`Bid sent. ${bid.check.label}`);
    } catch (e) { alert(e.message); }
    loadOpenJobs();
  }));
  $("#openJobs").querySelectorAll("[data-after]").forEach((inp) => inp.addEventListener("change", async () => {
    const fd = new FormData();
    fd.append("photo", await shrink(inp.files[0]));
    typing = true;
    inp.nextElementSibling.textContent = "⏳ Gemma 4 Verifier agent is comparing before and after…";
    try { await api(`/api/jobs/${inp.dataset.after}/verify`, { method: "POST", body: fd }); }
    catch (e) { alert(e.message); }
    typing = false;
    loadOpenJobs();
  }));
}

// ---------- boot ----------
function refresh() {
  if (!$("#customer").classList.contains("hidden")) loadMyJobs();
  else loadOpenJobs();
}

(async () => {
  SECTIONS = await api("/api/sections");
  renderTiles();
  api("/api/health").then((h) => ($("#modelName").textContent = h.model)).catch(() => {});
  if (store.get("rozgar.role", "customer") === "mistri") document.querySelector('[data-role="mistri"]').click();
  refresh();
  setInterval(refresh, 4000);
})();
