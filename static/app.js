const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const rs = (n) => "Rs " + Math.round(n).toLocaleString("en-PK");
const store = {
  get(k, d) { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
const icon = (name, cls = "ic") => `<img class="${cls}" src="/static/img/${name}.png" alt="">`;

let lang = store.get("rozgar.lang", "en");
let role = store.get("rozgar.role", "customer");
const t = (k) => I18N[lang][k] ?? I18N.en[k] ?? k;
const L = (obj) => (obj && typeof obj === "object" ? obj[lang] || obj.en : obj ?? "");

let SECTIONS = {}, CATALOG = {}, AREAS = [];
const sel = { section: "", sub: "", answers: {}, symptoms: [] };
let lastDiagnosis = null;
let invited = new Set();
let myJobIds = store.get("rozgar.myJobs", []);
let lastMyHTML = "", lastOpenHTML = "", typing = false;

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

// ---------- language and role ----------
function applyLang() {
  document.documentElement.lang = lang === "ur" ? "ur" : "en";
  document.documentElement.dir = lang === "ur" ? "rtl" : "ltr";
  document.body.classList.toggle("urdu-ui", lang === "ur");
  document.querySelectorAll("[data-t]").forEach((el) => (el.textContent = t(el.dataset.t)));
  document.querySelectorAll("[data-tp]").forEach((el) => (el.placeholder = t(el.dataset.tp)));
  document.querySelectorAll("#langSeg button").forEach((b) => b.classList.toggle("active", b.dataset.lang === lang));
  document.querySelectorAll("#roleSeg button").forEach((b) => b.classList.toggle("active", b.dataset.role === role));
  renderSections();
  renderSubs();
  renderDetails();
  renderWorkerSelect();
  if (lastDiagnosis) renderResult(lastDiagnosis);
  lastMyHTML = lastOpenHTML = "";
  refresh();
}

document.querySelectorAll("#langSeg button").forEach((b) => b.addEventListener("click", () => {
  lang = b.dataset.lang;
  store.set("rozgar.lang", lang);
  applyLang();
}));
document.querySelectorAll("#roleSeg button").forEach((b) => b.addEventListener("click", () => {
  role = b.dataset.role;
  store.set("rozgar.role", role);
  $("#customer").classList.toggle("hidden", role !== "customer");
  $("#worker").classList.toggle("hidden", role !== "worker");
  applyLang();
}));

// ---------- step 1: sections ----------
function renderSections() {
  $("#sections").innerHTML = Object.entries(SECTIONS).map(([id, s]) =>
    `<button class="sec ${sel.section === id ? "active" : ""}" data-s="${id}">${icon(s.icon, "sec-ic")}<span class="n">${esc(L(s.name))}</span><span class="w">${esc(L(s.worker))}</span></button>`
  ).join("");
  $("#sections").querySelectorAll(".sec").forEach((b) => b.addEventListener("click", () => {
    const id = b.dataset.s;
    sel.section = sel.section === id ? "" : id;
    sel.sub = ""; sel.answers = {}; sel.symptoms = [];
    renderSections(); renderSubs(); renderDetails();
    if (sel.section) $("#subBlock").scrollIntoView({ behavior: "smooth", block: "start" });
  }));
}

// ---------- step 2: sub-problem ----------
function renderSubs() {
  $("#subBlock").classList.toggle("hidden", !sel.section);
  $("#detailsNum").textContent = sel.section ? "3" : "2";
  $("#notSure").classList.toggle("hidden", !!sel.section);
  if (!sel.section) return;
  $("#subs").innerHTML = CATALOG[sel.section].map((s) =>
    `<button class="chip ${sel.sub === s.id ? "active" : ""}" data-sub="${s.id}">${esc(L(s.label))}</button>`
  ).join("");
  $("#subs").querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
    sel.sub = b.dataset.sub; sel.answers = {}; sel.symptoms = [];
    renderSubs(); renderDetails();
  }));
}

// ---------- step 3: guided questions, symptoms, photo ----------
const currentSub = () => (sel.section && sel.sub ? CATALOG[sel.section].find((s) => s.id === sel.sub) : null);

function renderDetails() {
  const sub = currentSub();
  $("#questions").innerHTML = (sub?.questions || []).map((q) =>
    `<div class="qlabel">${esc(L(q.label))}</div><div class="chips">${q.options.map((o) =>
      `<button class="chip ${sel.answers[q.id] === o.v ? "active" : ""}" data-q="${q.id}" data-v="${o.v}">${esc(L(o.label))}</button>`).join("")}</div>`
  ).join("");
  $("#questions").querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
    sel.answers[b.dataset.q] = b.dataset.v;
    renderDetails();
  }));

  const syms = sub?.symptoms || [];
  $("#symptomsBlock").classList.toggle("hidden", !syms.length);
  $("#symptoms").innerHTML = syms.map((s) =>
    `<button class="chip check ${sel.symptoms.includes(s.v) ? "active" : ""}" data-sym="${s.v}">${esc(L(s.label))}</button>`).join("");
  $("#symptoms").querySelectorAll(".chip").forEach((b) => b.addEventListener("click", () => {
    const v = b.dataset.sym;
    sel.symptoms = sel.symptoms.includes(v) ? sel.symptoms.filter((x) => x !== v) : [...sel.symptoms, v];
    renderDetails();
  }));

  const mode = sub?.photo || "yes";
  $("#photoHint").classList.toggle("hidden", mode === "yes");
  $("#photoHint").innerHTML = mode === "label" ? icon("camera") + esc(t("photo_label")) : mode === "no" ? icon("clipboard") + esc(t("photo_no")) : "";
  $("#photoLabel").textContent = mode === "yes" ? t("photo_yes") : t("photo_add");
}

$("#photo").addEventListener("change", () => {
  const f = $("#photo").files[0];
  if (!f) return;
  $("#preview").src = URL.createObjectURL(f);
  $("#preview").classList.remove("hidden");
});

// ---------- diagnose ----------
let progTimer;
function showProgress(on) {
  const el = $("#progress");
  clearInterval(progTimer);
  if (!on) { el.classList.add("hidden"); return; }
  const steps = [sel.section ? null : "step_router", "step_spec", "step_price", "step_safety"].filter(Boolean);
  let i = 0;
  const draw = () => { el.innerHTML = steps.map((s, j) => `<div class="step ${j < i ? "done" : j === i ? "run" : ""}">${esc(t(s))}</div>`).join(""); };
  el.classList.remove("hidden");
  draw();
  progTimer = setInterval(() => { if (i < steps.length - 3) { i++; draw(); } }, 9000);
}

async function diagnose(answer = "") {
  const f = $("#photo").files[0];
  if (!f && !$("#desc").value.trim() && !sel.sub) { alert(t("need_input")); return; }
  const fd = new FormData();
  if (f) fd.append("photo", await shrink(f));
  fd.append("description", $("#desc").value);
  fd.append("section", sel.section);
  fd.append("subcategory", sel.sub);
  fd.append("answers", JSON.stringify(sel.answers));
  fd.append("symptoms", JSON.stringify(sel.symptoms));
  fd.append("lang", lang);
  if (answer) fd.append("answer", answer);

  const btn = $("#diagnoseBtn");
  btn.disabled = true;
  btn.textContent = t("working");
  $("#clarify").classList.add("hidden");
  $("#result").classList.add("hidden");
  showProgress(true);
  try {
    const d = await api("/api/diagnose", { method: "POST", body: fd });
    if (d.status === "clarify") {
      $("#clarify").innerHTML = `<b>${esc(t("clarify"))}</b> ${esc(d.question)}<input id="clarifyAns" placeholder="${esc(t("your_answer"))}"><button class="secondary" id="clarifyBtn">${esc(t("clarify_btn"))}</button>`;
      $("#clarify").classList.remove("hidden");
      $("#clarifyBtn").onclick = () => diagnose($("#clarifyAns").value);
      return;
    }
    lastDiagnosis = d;
    invited = new Set();
    renderResult(d);
    $("#result").scrollIntoView({ behavior: "smooth" });
  } catch (e) {
    $("#result").innerHTML = `<div class="card danger">${icon("cross")}<b>${esc(t("err_title"))}</b> ${esc(e.message)}<br><span class="small">${esc(t("err_hint"))}</span></div>`;
    $("#result").classList.remove("hidden");
  } finally {
    btn.disabled = false;
    btn.textContent = t("diagnose");
    showProgress(false);
  }
}
$("#diagnoseBtn").addEventListener("click", () => diagnose());

// ---------- result ----------
function safetyHTML(s) {
  if (s.level === "none") return "";
  const cls = s.level === "danger" ? "danger" : "warn";
  return `<div class="card ${cls} row-ic">${icon("warning", "ic-lg")}<div><b>${esc(t(s.level === "danger" ? "danger" : "caution"))}</b><br>${esc(s.advice)}<br><b>${esc(t("helpline"))}:</b> ${s.helplines.map(esc).join(" · ")}</div></div>`;
}

function priceTable(p) {
  const rows = p.materials.map((m) => `<tr><td>${esc(m.item)} × ${m.qty}</td><td>${rs(m.total_pkr)}</td></tr>`).join("");
  const mult = p.size_multiplier && p.size_multiplier !== 1 ? ` (${esc(t("size_mult"))}${p.size_multiplier})` : "";
  return `<table>${rows}
    <tr><td>${esc(t("labour"))} ${p.labour_hours} h × ${rs(p.labour_per_hour)}${mult}</td><td>${rs(p.labour_total)}</td></tr>
    <tr><td>${esc(t("visit_fee"))}</td><td>${rs(p.visit_fee)}</td></tr>
    <tr><td><b>${esc(t("base"))}</b></td><td><b>${rs(p.base)}</b></td></tr></table>`;
}

function traceHTML(trace) {
  return `<details class="trace-box"><summary>${icon("brain")}${esc(t("trace"))} (${trace.length} ${esc(t("steps"))})</summary><div class="trace">${trace.map((s) =>
    `<div class="t"><b>${esc(s.agent)}</b> <span class="muted">· ${esc(s.model)} · ${(s.ms / 1000).toFixed(1)}s${s.attempts > 1 ? ` · ${s.attempts} tries` : ""}</span><pre>${esc(JSON.stringify(s.output, null, 2))}</pre></div>`
  ).join("")}</div></details>`;
}

function checkHTML(status) {
  const k = { high: "chk_high", low: "chk_low", fair: "chk_fair" }[status];
  return `<span class="check ${status}">${icon(status === "fair" ? "check" : "warning")}${esc(t(k))}</span>`;
}
function priceStatus(v, p) {
  if (v > p.fair_max * 1.25) return "high";
  if (v < p.fair_min * 0.75) return "low";
  return "fair";
}

const stars = (r) => `<span class="stars">${icon("star", "star")}${Number(r).toFixed(1)}</span>`;

function workerCard(w, { inviteBtn = false } = {}) {
  const projects = w.past_projects.map((p) => `<li>${p.after_photo ? `<img class="thumb sm" src="${imgSrc(p.after_photo)}">` : ""}<div><b>${esc(L(p.title))}</b>${p.verified_by_gemma ? ` <span class="badge ok">${icon("check")}Gemma</span>` : ""}<br><span class="small muted">${esc(p.area)} · ${rs(p.price)} · ${"★".repeat(p.rating)}${p.review ? ` · "${esc(L(p.review))}"` : ""}</span></div></li>`).join("");
  return `<div class="wcard">
    <div class="row-ic">${icon("worker", "avatar")}<div class="grow">
      <b>${esc(L(w.name))}</b> ${w.verified ? `<span class="badge ok">${icon("check")}${esc(t("verified"))}</span>` : ""}<br>
      <span class="small muted">${icon("pin")}${esc(w.area)} · ${stars(w.rating)} · ${w.jobs_done} ${esc(t("jobs_done"))} · ${w.years} ${esc(t("years"))}</span>
    </div>${inviteBtn ? `<button class="secondary ${invited.has(w.id) ? "on" : ""}" data-invite="${w.id}">${esc(t(invited.has(w.id) ? "invited" : "invite"))}</button>` : ""}</div>
    <details><summary class="small">${esc(t("past_projects"))} (${w.past_projects.length})</summary><ul class="projects">${projects}</ul></details>
  </div>`;
}

async function renderResult(d) {
  const s = d.section_info, p = d.price;
  const danger = d.safety.level === "danger";
  const prevPrice = $("#offer")?.value || "";
  const area = $("#area").value;
  const workers = await api(`/api/workers?section=${d.section}&area=${encodeURIComponent(area)}`).catch(() => []);
  $("#result").innerHTML = `
    ${safetyHTML(d.safety)}
    <div class="card">
      <div class="row-ic">${icon(s.icon, "ic-lg")}<div><span class="muted small">${esc(t("goes_to"))}</span><br><b>${esc(L(s.worker))}</b> · ${esc(L(s.name))}</div></div>
      <h3>${esc(d.diagnosis)}</h3>
      <p class="muted">${esc(d.likely_cause)} · ${esc(t("difficulty"))}: ${esc(t(d.difficulty))}</p>
      <div class="estimate row-ic">${icon("money", "ic-lg")}<div><div class="muted small">${esc(t("estimate"))}</div><div class="price">${rs(p.fair_min)} – ${rs(p.fair_max)}</div></div></div>
      <details><summary>${esc(t("how_calc"))}</summary>${priceTable(p)}<p class="small muted">${esc(t("calc_note"))}</p></details>
      <div class="card inner"><div class="muted small">${esc(t("job_card"))}</div><div class="jobcard ${lang === "ur" ? "urdu" : ""}">${esc(d.job_card_urdu)}</div></div>

      <div class="qlabel">${esc(t("your_price"))}</div>
      <div class="price-input"><span>Rs</span><input type="number" inputmode="numeric" id="offer" min="0" step="50" value="${esc(prevPrice)}" placeholder="${esc(t("price_ph"))}"></div>
      <div id="offerChk"></div>
      <p class="note">${esc(t("price_note"))}</p>

      <h3>${esc(t("workers_near"))} <span class="small muted">(${esc(t("sample_note"))})</span></h3>
      <div id="workerList">${workers.map((w) => workerCard(w, { inviteBtn: true })).join("")}</div>

      <button class="primary" id="postBtn">${esc(danger ? t("post_danger") : invited.size ? t("post_invited") : t("post_all"))}</button>
      ${traceHTML(d.trace)}
    </div>`;
  $("#result").classList.remove("hidden");
  const upd = () => {
    const v = +$("#offer").value;
    $("#offerChk").innerHTML = v ? checkHTML(priceStatus(v, p)) : "";
  };
  $("#offer").addEventListener("input", upd);
  upd();
  $("#workerList").querySelectorAll("[data-invite]").forEach((b) => b.addEventListener("click", () => {
    const id = b.dataset.invite;
    invited.has(id) ? invited.delete(id) : invited.add(id);
    renderResult(d);
  }));
  $("#postBtn").addEventListener("click", postJob);
}

async function postJob() {
  const offer = +$("#offer").value;
  if (!offer) { alert(t("enter_price")); $("#offer").focus(); return; }
  if (lastDiagnosis.safety.level === "danger" && !confirm(t("confirm_danger"))) return;
  try {
    const job = await postJSON("/api/jobs", { diagnosis: lastDiagnosis, offer, area: $("#area").value, invited: [...invited] });
    myJobIds.unshift(job.id);
    store.set("rozgar.myJobs", myJobIds);
    $("#result").innerHTML = `<div class="card ok row-ic">${icon("check", "ic-lg")}<b>${esc(t("posted"))}</b></div>`;
    $("#photo").value = ""; $("#desc").value = ""; $("#preview").classList.add("hidden");
    lastDiagnosis = null;
    loadMyJobs();
  } catch (e) { alert(e.message); }
}

// ---------- my jobs ----------
const statusText = (j) => t({ open: "st_open", in_progress: "st_in_progress", verified: "st_verified" }[j.status]);

function verificationHTML(j) {
  const v = j.verification;
  if (!v) return "";
  const ok = v.fixed === "yes";
  const label = t(ok ? "gemma_ok" : v.fixed === "no" ? "gemma_no" : "gemma_unclear");
  return `<div class="card ${ok ? "ok" : "danger"}"><div class="row-ic">${icon(ok ? "check" : "cross", "ic-lg")}<div><b>${esc(label)}</b> <span class="small">(${Math.round(v.confidence * 100)}% ${esc(t("confident"))})</span><br>${esc(v.evidence)}</div></div>
    <div class="row" style="margin-top:8px">${j.photo ? `<div><div class="small">${esc(t("before"))}</div><img class="thumb" src="${imgSrc(j.photo)}"></div>` : ""}<div><div class="small">${esc(t("after"))}</div><img class="thumb" src="${imgSrc(v.after_photo)}"></div></div></div>`;
}

function jobHead(j) {
  const s = j.section_info;
  return `<div class="row-ic">${j.photo ? `<img class="thumb" src="${imgSrc(j.photo)}">` : icon(s.icon, "thumb ic-box")}<div class="grow">
    <span class="badge">${esc(L(s.worker))}</span> <span class="small status ${j.status}">${esc(statusText(j))}</span>
    <h3>${esc(j.diagnosis)}</h3>
    <div class="small muted">${icon("pin")}${esc(j.area || "Faisalabad")} · ${esc(t("your_offer"))} <b>${rs(j.offer)}</b> · ${esc(t("estimate"))} ${rs(j.price.fair_min)} – ${rs(j.price.fair_max)}</div></div></div>`;
}

async function loadMyJobs() {
  if (!myJobIds.length) { $("#myJobs").innerHTML = `<p class="muted">${esc(t("no_jobs"))}</p>`; return; }
  const jobs = (await Promise.all(myJobIds.map((id) => api(`/api/jobs/${id}`).catch(() => null)))).filter(Boolean);
  const html = jobs.length ? jobs.map((j) => {
    const bids = j.bids.map((b) => `<div class="bid">${workerCard(b.worker)}<div class="bid-side"><b class="price-sm">${rs(b.amount)}</b>${checkHTML(b.check.status)}
      ${j.status === "open" ? `<button class="primary sm" data-accept="${j.id}:${b.id}">${esc(t("accept"))}</button>` : j.accepted?.id === b.id ? `<span class="badge ok">${esc(t("accepted"))}</span>` : ""}</div></div>`).join("");
    return `<div class="card">${jobHead(j)}
      ${bids || (j.status === "open" ? `<p class="muted small">${esc(t("waiting_bids"))}</p>` : "")}
      ${verificationHTML(j)}${traceHTML(j.trace)}</div>`;
  }).join("") : `<p class="muted">${esc(t("no_jobs"))}</p>`;
  if (html === lastMyHTML) return;
  lastMyHTML = html;
  $("#myJobs").innerHTML = html;
  $("#myJobs").querySelectorAll("[data-accept]").forEach((b) => b.addEventListener("click", async () => {
    const [jobId, bidId] = b.dataset.accept.split(":");
    await postJSON(`/api/jobs/${jobId}/accept`, { bid_id: bidId }).catch((e) => alert(e.message));
    loadMyJobs();
  }));
}

// ---------- worker view ----------
let WORKERS = [];
let me = store.get("rozgar.workerId", "");

function renderWorkerSelect() {
  if (!WORKERS.length) return;
  if (!me) me = WORKERS[0].id;
  $("#workerSel").innerHTML = Object.entries(SECTIONS).map(([sid, s]) =>
    `<optgroup label="${esc(L(s.worker))}">${WORKERS.filter((w) => w.section === sid).map((w) =>
      `<option value="${w.id}" ${w.id === me ? "selected" : ""}>${esc(L(w.name))} · ${esc(w.area)}</option>`).join("")}</optgroup>`
  ).join("");
  const w = WORKERS.find((x) => x.id === me);
  $("#workerProfile").innerHTML = w ? workerCard(w) : "";
}
$("#workerSel").addEventListener("change", () => {
  me = $("#workerSel").value;
  store.set("rozgar.workerId", me);
  lastOpenHTML = "";
  renderWorkerSelect();
  loadOpenJobs();
});

async function loadOpenJobs() {
  if (typing) return;
  const w = WORKERS.find((x) => x.id === me);
  if (!w) return;
  let jobs = await api(`/api/jobs?section=${w.section}`).catch(() => []);
  jobs = jobs.sort((a, b) => (b.invited || []).includes(me) - (a.invited || []).includes(me));
  const html = jobs.length ? jobs.map((j) => {
    const mine = j.accepted && j.accepted.worker_id === me;
    const myBid = j.bids.find((b) => b.worker_id === me);
    let action = "";
    if (j.status === "open" && !myBid) {
      action = `<div class="price-input"><span>Rs</span><input type="number" inputmode="numeric" placeholder="${esc(t("bid_ph"))}" data-bid="${j.id}"></div><div data-bidchk="${j.id}"></div><button class="primary" data-send="${j.id}">${esc(t("send_bid"))}</button>`;
    } else if (myBid && j.status === "open") {
      action = `<p class="small">${esc(t("bid_sent"))} <b>${rs(myBid.amount)}</b> ${checkHTML(myBid.check.status)}</p>`;
    } else if (j.status === "in_progress" && mine) {
      action = `<label class="upload">${icon("camera")}<input type="file" accept="image/*" data-after="${j.id}"><span>${esc(t("upload_after"))}</span></label>`;
    }
    return `<div class="card">${(j.invited || []).includes(me) ? `<span class="badge ok">${esc(t("invited_you"))}</span>` : ""}
      ${j.safety.level === "danger" ? `<div class="card danger row-ic">${icon("warning")}<b>${esc(t("danger"))}</b></div>` : ""}
      ${jobHead(j).replace(esc(t("your_offer")), esc(t("customer_pays")))}
      <div class="card inner"><div class="muted small">${esc(t("job_card"))}</div><div class="jobcard ${j.lang === "ur" ? "urdu" : ""}">${esc(j.job_card_urdu)}</div>
      <div class="small muted">${esc(t("bring"))}: ${j.price.materials.map((m) => esc(m.item)).join(", ") || "-"}</div></div>
      ${action}${verificationHTML(j)}</div>`;
  }).join("") : `<p class="muted">${esc(t("w_none"))}</p>`;
  if (html === lastOpenHTML) return;
  lastOpenHTML = html;
  $("#openJobs").innerHTML = html;

  $("#openJobs").querySelectorAll("[data-bid]").forEach((inp) => {
    const j = jobs.find((x) => x.id === inp.dataset.bid);
    inp.addEventListener("focus", () => (typing = true));
    inp.addEventListener("blur", () => setTimeout(() => (typing = false), 300));
    inp.addEventListener("input", () => {
      const v = +inp.value;
      $(`[data-bidchk="${j.id}"]`).innerHTML = v ? checkHTML(priceStatus(v, j.price)) : "";
    });
  });
  $("#openJobs").querySelectorAll("[data-send]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.send;
    const amount = +$(`[data-bid="${id}"]`).value;
    if (!amount) { alert(t("enter_price")); return; }
    typing = false;
    await postJSON(`/api/jobs/${id}/bids`, { worker_id: me, amount }).catch((e) => alert(e.message));
    loadOpenJobs();
  }));
  $("#openJobs").querySelectorAll("[data-after]").forEach((inp) => inp.addEventListener("change", async () => {
    const fd = new FormData();
    fd.append("photo", await shrink(inp.files[0]));
    typing = true;
    inp.nextElementSibling.textContent = t("verifying");
    try { await api(`/api/jobs/${inp.dataset.after}/verify`, { method: "POST", body: fd }); }
    catch (e) { alert(e.message); }
    typing = false;
    WORKERS = await api("/api/workers").catch(() => WORKERS);
    renderWorkerSelect();
    loadOpenJobs();
  }));
}

// ---------- boot ----------
function refresh() {
  if (role === "customer") loadMyJobs();
  else loadOpenJobs();
}

(async () => {
  [SECTIONS, CATALOG, AREAS, WORKERS] = await Promise.all([api("/api/sections"), api("/api/catalog"), api("/api/areas"), api("/api/workers")]);
  $("#area").innerHTML = AREAS.map((a) => `<option>${esc(a)}</option>`).join("");
  $("#area").value = store.get("rozgar.area", AREAS[0]);
  $("#area").addEventListener("change", () => { store.set("rozgar.area", $("#area").value); if (lastDiagnosis) renderResult(lastDiagnosis); });
  $("#customer").classList.toggle("hidden", role !== "customer");
  $("#worker").classList.toggle("hidden", role !== "worker");
  api("/api/health").then((h) => ($("#modelName").textContent = h.model)).catch(() => {});
  applyLang();
  setInterval(refresh, 4000);
})();
