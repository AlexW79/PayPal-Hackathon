/* SplitSnap front end: plain JS, no build step. */
const $ = (id) => document.getElementById(id);
const state = { receipt: null, split: null, config: null };

// ---------------- helpers ----------------
function money(cents, currency) {
  const cur = currency || state.receipt?.currency || "USD";
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: cur, currencyDisplay: "narrowSymbol" }).format(cents / 100);
  } catch { return `${(cents / 100).toFixed(2)} ${cur}`; }
}
const toCents = (v) => Math.round((Number(v) || 0) * 100);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let toastTimer;
function toast(msg, isError = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast" + (isError ? " error" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.add("hidden"), isError ? 6000 : 3000);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  let body = null;
  try { body = await res.json(); } catch { /* empty */ }
  if (!res.ok) throw new Error(body?.detail ? (typeof body.detail === "string" ? body.detail : "Request was not valid.") : `Server error (${res.status})`);
  return body;
}

function goTo(step) {
  for (let i = 1; i <= 4; i++) $(`screen-${i}`).classList.toggle("hidden", i !== step);
  document.querySelectorAll("#stepper li").forEach((li) => {
    const n = Number(li.dataset.step);
    li.classList.toggle("active", n === step);
    li.classList.toggle("done", n < step);
  });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function setBusy(btn, busy, label) {
  if (busy) {
    btn.dataset.label = btn.innerHTML;
    btn.innerHTML = `<span class="spinner ${btn.classList.contains("btn-pay") ? "dark" : ""}"></span> ${label}`;
    btn.disabled = true;
  } else {
    btn.innerHTML = btn.dataset.label || btn.innerHTML;
    btn.disabled = false;
  }
}

// ---------------- config badges ----------------
async function loadConfig() {
  try {
    state.config = await api("/api/config");
    const b = [];
    b.push(state.config.ai === "gemini"
      ? `<span class="badge">AI: Gemini</span>`
      : `<span class="badge warn" title="Add GEMINI_API_KEY to .env">AI: demo mode</span>`);
    b.push(state.config.paypal === "demo"
      ? `<span class="badge warn" title="Add PayPal sandbox keys to .env">PayPal: demo mode</span>`
      : `<span class="badge">PayPal ${esc(state.config.paypal)}</span>`);
    $("modeBadges").innerHTML = b.join("");
  } catch { /* server not reachable: errors show on use */ }
}

// ---------------- step 1: image in ----------------
async function shrinkImage(file, maxSide = 1600) {
  // Phones produce huge photos; resize in the browser before upload.
  try {
    const bmp = await createImageBitmap(file);
    const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bmp.width * scale);
    canvas.height = Math.round(bmp.height * scale);
    canvas.getContext("2d").drawImage(bmp, 0, 0, canvas.width, canvas.height);
    return await new Promise((r) => canvas.toBlob(r, "image/jpeg", 0.88));
  } catch {
    return file; // e.g. HEIC on some browsers: send as-is
  }
}

async function handleImage(file) {
  if (!file) return;
  if (!file.type.startsWith("image/")) return toast("Please choose an image file.", true);
  const blob = await shrinkImage(file);
  $("previewImg").src = URL.createObjectURL(blob);
  $("dropzone").classList.add("hidden");
  $("scanning").classList.remove("hidden");
  $("receiptCard").classList.add("hidden");
  try {
    const form = new FormData();
    form.append("image", blob, "bill.jpg");
    const receipt = await api("/api/receipt", { method: "POST", body: form });
    showReceipt(receipt);
  } catch (e) {
    toast(e.message, true);
    $("dropzone").classList.remove("hidden");
  } finally {
    $("scanning").classList.add("hidden");
  }
}

function showReceipt(receipt) {
  state.receipt = receipt;
  $("scanCard").classList.add("hidden");
  $("dropzone").classList.add("hidden");
  $("scanning").classList.add("hidden");
  $("receiptCard").classList.remove("hidden");
  $("merchantName").textContent = receipt.merchant || "Your bill";
  $("receiptMeta").textContent = [receipt.date, `${receipt.items.length} items`, receipt.currency].filter(Boolean).join(" · ");
  renderItems();
}

function renderItems() {
  const r = state.receipt;
  $("itemsList").innerHTML = r.items.map((it, i) => `
    <li>
      <input class="qty" data-i="${i}" data-k="quantity" value="${it.quantity}" inputmode="numeric" aria-label="Quantity">
      <input data-i="${i}" data-k="name" value="${esc(it.name)}" aria-label="Item name">
      <input class="price" data-i="${i}" data-k="total_price" value="${Number(it.total_price).toFixed(2)}" inputmode="decimal" aria-label="Price">
      <button class="icon-btn" data-del="${i}" type="button" aria-label="Remove item">✕</button>
    </li>`).join("");
  renderTotals();
}

function renderTotals() {
  const r = state.receipt;
  const items = r.items.reduce((s, it) => s + toCents(it.total_price), 0);
  const extras = toCents(r.tax) + toCents(r.tip) + toCents(r.service_charge) - toCents(r.discount);
  const total = items + extras;
  const row = (label, key) => `<div class="row"><span>${label}</span><input data-t="${key}" value="${(Number(r[key]) || 0).toFixed(2)}" inputmode="decimal" aria-label="${label}"></div>`;
  const printed = toCents(r.total);
  const mismatch = printed && Math.abs(printed - total) > 1;
  $("totalsBox").innerHTML = `
    <div class="row"><span>Items</span><span>${money(items)}</span></div>
    ${row("Tax", "tax")}${row("Tip", "tip")}${row("Service charge", "service_charge")}${row("Discount", "discount")}
    <div class="row grand"><span>Total</span><span>${money(total)}</span></div>
    ${mismatch ? `<div class="warn-text">The bill says ${money(printed)}. Check the items and extras above.</div>` : ""}`;
}

$("itemsList").addEventListener("input", (e) => {
  const { i, k } = e.target.dataset;
  if (i === undefined) return;
  const it = state.receipt.items[Number(i)];
  it[k] = k === "name" ? e.target.value : k === "quantity" ? (parseInt(e.target.value, 10) || 1) : (parseFloat(e.target.value) || 0);
  renderTotals();
});
$("itemsList").addEventListener("click", (e) => {
  const d = e.target.dataset.del;
  if (d === undefined) return;
  state.receipt.items.splice(Number(d), 1);
  renderItems();
});
$("totalsBox").addEventListener("change", (e) => {
  const k = e.target.dataset.t;
  if (!k) return;
  state.receipt[k] = parseFloat(e.target.value) || 0;
  renderTotals();
});
$("btnAddItem").onclick = () => {
  state.receipt.items.push({ name: "New item", quantity: 1, total_price: 0 });
  renderItems();
  const inputs = $("itemsList").querySelectorAll("li:last-child input");
  inputs[1]?.select();
};
$("btnRescan").onclick = () => {
  $("receiptCard").classList.add("hidden");
  $("scanCard").classList.remove("hidden");
  $("dropzone").classList.remove("hidden");
};

$("btnUpload").onclick = () => $("fileInput").click();
$("fileInput").onchange = (e) => { handleImage(e.target.files[0]); e.target.value = ""; };
$("captureInput").onchange = (e) => { handleImage(e.target.files[0]); e.target.value = ""; };
$("btnSample").onclick = async () => {
  try { showReceipt(await api("/api/sample-receipt")); } catch (e) { toast(e.message, true); }
};

// drag & drop
const dz = $("dropzone");
["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
dz.addEventListener("drop", (e) => handleImage(e.dataTransfer.files[0]));

// camera: phones use the native camera; desktops get a live webcam view
let stream = null;
$("btnCamera").onclick = async () => {
  const touch = window.matchMedia("(pointer: coarse)").matches;
  if (touch || !navigator.mediaDevices?.getUserMedia) return $("captureInput").click();
  try {
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment", width: { ideal: 1920 } } });
    $("video").srcObject = stream;
    $("cameraModal").classList.remove("hidden");
  } catch {
    toast("Couldn't open the camera, so choose a photo instead.", true);
    $("captureInput").click();
  }
};
function closeCamera() {
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  $("cameraModal").classList.add("hidden");
}
$("btnCamCancel").onclick = closeCamera;
$("btnShutter").onclick = () => {
  const v = $("video");
  const c = document.createElement("canvas");
  c.width = v.videoWidth; c.height = v.videoHeight;
  c.getContext("2d").drawImage(v, 0, 0);
  closeCamera();
  c.toBlob((b) => handleImage(new File([b], "photo.jpg", { type: "image/jpeg" })), "image/jpeg", 0.92);
};

$("btnToStep2").onclick = () => {
  if (!state.receipt?.items.length) return toast("Add at least one item.", true);
  state.receipt.items = state.receipt.items.filter((it) => it.name.trim());
  $("itemChips").innerHTML = state.receipt.items.map((it) =>
    `<button class="chip" type="button" data-chip="${esc(it.name)}">${esc(it.name)}</button>`).join("");
  goTo(2);
};

// ---------------- step 2: describe (type or speak) ----------------
$("itemChips").addEventListener("click", (e) => {
  const name = e.target.dataset.chip;
  if (!name) return;
  const ta = $("description");
  ta.value = (ta.value.trim() ? ta.value.trim() + " " : "") + `the ${name} `;
  ta.focus();
});

const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognizer = null, recorder = null, recording = false;

function setMicUI(on, msg) {
  recording = on;
  $("btnMic").classList.toggle("recording", on);
  $("micStatus").textContent = msg;
}

$("btnMic").onclick = async () => {
  if (recording) {
    recognizer?.stop();
    if (recorder && recorder.state === "recording") recorder.stop();
    return;
  }
  const ta = $("description");
  if (SpeechRec) {
    // Built-in browser speech recognition (Chrome, Edge, Safari): live text as you talk.
    recognizer = new SpeechRec();
    recognizer.lang = navigator.language || "en-US";
    recognizer.continuous = true;
    recognizer.interimResults = true;
    const base = ta.value.trim() ? ta.value.trim() + " " : "";
    recognizer.onresult = (ev) => {
      let text = "";
      for (let i = 0; i < ev.results.length; i++) text += ev.results[i][0].transcript;
      ta.value = base + text;
    };
    recognizer.onerror = (ev) => {
      if (ev.error === "not-allowed") toast("Microphone permission was blocked.", true);
    };
    recognizer.onend = () => setMicUI(false, "Got it. Edit the text if anything's off.");
    recognizer.start();
    setMicUI(true, "Listening… tap the mic again when you're done.");
    return;
  }
  // Fallback (e.g. Firefox): record audio and let Gemini transcribe it.
  try {
    const s = await navigator.mediaDevices.getUserMedia({ audio: true });
    const type = ["audio/ogg", "audio/mp4", "audio/webm"].find((t) => MediaRecorder.isTypeSupported(t)) || "";
    recorder = new MediaRecorder(s, type ? { mimeType: type } : undefined);
    const chunks = [];
    recorder.ondataavailable = (e) => chunks.push(e.data);
    recorder.onstop = async () => {
      s.getTracks().forEach((t) => t.stop());
      setMicUI(false, "Transcribing…");
      try {
        const form = new FormData();
        form.append("audio", new Blob(chunks, { type: recorder.mimeType }), "voice");
        const { text } = await api("/api/transcribe", { method: "POST", body: form });
        ta.value = (ta.value.trim() ? ta.value.trim() + " " : "") + text;
        $("micStatus").textContent = "Got it. Edit the text if anything's off.";
      } catch (e) {
        toast(e.message, true);
        $("micStatus").textContent = "Couldn't transcribe. Try typing instead.";
      }
    };
    recorder.start();
    setMicUI(true, "Recording… tap the mic again to stop.");
  } catch {
    toast("Couldn't access the microphone.", true);
  }
};

$("btnBack1").onclick = () => goTo(1);

$("btnSplit").onclick = async () => {
  const description = $("description").value.trim();
  if (!description) return toast("Tell us who had what first.", true);
  const btn = $("btnSplit");
  setBusy(btn, true, "Working it out…");
  try {
    state.payer = $("payerName").value.trim() || "Me";
    state.split = await api("/api/split", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ receipt: state.receipt, description, payer: state.payer }),
    });
    renderReview();
    goTo(3);
  } catch (e) {
    toast(e.message, true);
  } finally {
    setBusy(btn, false);
  }
};

// ---------------- step 3: review ----------------
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function renderReview() {
  const s = state.split;
  const others = s.people.filter((p) => !p.is_me);
  const owed = others.reduce((n, p) => n + p.total_cents, 0);

  const notes = [];
  if (s.notes) notes.push(esc(s.notes));
  if (s.added_tip_percent) notes.push(`Added a ${s.added_tip_percent}% tip as you asked.`);
  $("aiNotes").innerHTML = notes.length ? `<b>AI notes:</b> ${notes.join(" ")}` : "";
  $("aiNotes").classList.toggle("hidden", !notes.length);
  $("aiNotes").classList.toggle("warn", !!s.unassigned_items.length);

  $("summary").innerHTML = `
    <div><div class="label">Bill total</div><div class="value">${money(s.total_cents, s.currency)}</div></div>
    <div><div class="label">You'll get back</div><div class="value">${money(owed, s.currency)}</div></div>`;

  $("peopleCards").innerHTML = s.people.map((p, idx) => `
    <div class="card person">
      <div class="person-head">
        <div class="avatar ${p.is_me ? "me" : ""}">${esc(p.name.charAt(0).toUpperCase())}</div>
        <div>
          <div class="person-name">${esc(p.name)}${p.is_me ? " (you)" : ""}</div>
          <div class="person-sub">${p.is_me ? "You paid the bill · no request needed" : `${p.lines.length} item${p.lines.length === 1 ? "" : "s"}`}</div>
        </div>
        <div class="person-total">${money(p.total_cents, s.currency)}</div>
      </div>
      <ul class="lines">
        ${p.lines.map((l) => `<li><span>${esc(l.label)}</span><span>${money(l.cents, s.currency)}</span></li>`).join("")}
        ${p.extras_cents ? `<li class="extra"><span>Tax, tip &amp; service share</span><span>${money(p.extras_cents, s.currency)}</span></li>` : ""}
      </ul>
      ${p.is_me ? "" : `<div class="email-row"><input type="email" data-email="${idx}" value="${esc(p.email || "")}" placeholder="${esc(p.name)}'s PayPal email" aria-label="${esc(p.name)}'s email"></div>`}
    </div>`).join("");
  $("btnSend").disabled = others.length === 0;
}

$("peopleCards").addEventListener("input", (e) => {
  const i = e.target.dataset.email;
  if (i === undefined) return;
  state.split.people[Number(i)].email = e.target.value.trim();
  e.target.classList.remove("invalid");
});

$("btnBack2").onclick = () => goTo(2);

$("btnSend").onclick = async () => {
  let ok = true;
  document.querySelectorAll("[data-email]").forEach((inp) => {
    const valid = EMAIL_RE.test(inp.value.trim());
    inp.classList.toggle("invalid", !valid);
    if (!valid) ok = false;
  });
  if (!ok) return toast("Add a valid email for everyone you're requesting money from.", true);

  const btn = $("btnSend");
  setBusy(btn, true, "Sending requests…");
  try {
    const res = await api("/api/send", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ split: state.split, payer: state.payer, note: $("note").value }),
    });
    startTracking({
      results: res.results, mode: res.mode, currency: state.split.currency,
      merchant: state.split.merchant, payer: state.payer, sentAt: Date.now(),
      statuses: {}, drafts: {}, open: null,
    });
    goTo(4);
  } catch (e) {
    toast(e.message, true);
  } finally {
    setBusy(btn, false);
  }
};

// ---------------- step 4: live payment tracker + reminders ----------------
const TRACKER_KEY = "splitsnap.tracker";
const POLL_MS = 5000;
let pollTimer = null;
let pendingRender = false;

function saveTracker() {
  const t = state.tracker;
  try {
    localStorage.setItem(TRACKER_KEY, JSON.stringify({ ...t, drafts: {}, open: null }));
  } catch { /* storage may be unavailable */ }
}

function loadSavedTracker() {
  try {
    const t = JSON.parse(localStorage.getItem(TRACKER_KEY) || "null");
    return t && Array.isArray(t.results) && t.results.some((r) => r.ok) ? t : null;
  } catch { return null; }
}

const tracked = () => state.tracker.results.filter((r) => r.ok && r.invoice_id);
const isPaid = (r) => state.tracker.statuses[r.invoice_id]?.state === "paid";
const allPaid = () => tracked().length > 0 && tracked().every(isPaid);

function startTracking(tracker) {
  state.tracker = { drafts: {}, open: null, statuses: {}, ...tracker };
  saveTracker();
  renderTracker();
  pollStatuses();
  clearInterval(pollTimer);
  pollTimer = setInterval(pollStatuses, POLL_MS);
}

function stopTracking() {
  clearInterval(pollTimer);
  pollTimer = null;
}

async function pollStatuses() {
  const t = state.tracker;
  if (!t || !tracked().length) return stopTracking();
  try {
    const { statuses } = await api("/api/status", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invoice_ids: tracked().map((r) => r.invoice_id) }),
    });
    const before = JSON.stringify(t.statuses);
    const newlyPaid = tracked().filter((r) => !isPaid(r) && statuses[r.invoice_id]?.state === "paid");
    t.statuses = statuses;
    t.lastChecked = Date.now();
    newlyPaid.forEach((r) => toast(`🎉 ${r.name} just paid ${money(r.total_cents, t.currency)}!`));
    if (JSON.stringify(statuses) !== before) {
      saveTracker();
      // Don't wipe a reminder the user is typing; render once they're done.
      if (document.activeElement?.closest?.(".remind-panel")) pendingRender = true;
      else renderTracker();
    } else {
      renderTrackerHeader();
    }
    if (allPaid()) stopTracking();
  } catch {
    $("live").classList.add("off");
    $("liveText").textContent = "Offline";
  }
}

function renderTrackerHeader() {
  const t = state.tracker;
  const list = tracked();
  const total = list.reduce((n, r) => n + r.total_cents, 0);
  const collected = list.reduce((n, r) => {
    const s = t.statuses[r.invoice_id];
    return n + (s?.state === "paid" ? r.total_cents : Math.min(s?.paid_cents || 0, r.total_cents));
  }, 0);
  const paidCount = list.filter(isPaid).length;
  const pct = total ? Math.round((collected / total) * 100) : 0;
  const done = allPaid();

  $("tracker").classList.toggle("hidden", !list.length);
  $("collected").textContent = money(collected, t.currency);
  $("collectedOf").textContent = `of ${money(total, t.currency)}`;
  $("progressBar").style.width = `${pct}%`;
  $("progressBar").classList.toggle("full", done);
  document.querySelector("#tracker .progress").setAttribute("aria-valuenow", pct);
  const checked = t.lastChecked ? new Date(t.lastChecked).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }) : "…";
  $("progressText").textContent = `${paidCount} of ${list.length} paid · checked ${checked}`;
  $("live").classList.toggle("off", done || !pollTimer);
  $("liveText").textContent = done ? "All paid" : pollTimer ? "Live" : "Paused";

  const demo = t.mode === "demo";
  const failed = t.results.filter((r) => !r.ok).length;
  $("doneTitle").textContent = done ? "Everyone's paid you back! 🎉"
    : !list.length ? "Requests not sent" : demo ? "Requests ready (demo)" : "Requests sent!";
  $("doneLead").textContent = done ? `You've collected ${money(total, t.currency)}${t.merchant ? ` for ${t.merchant}` : ""}.`
    : demo ? "Demo mode: nothing was sent. Use “Simulate payment” to see the tracker update."
    : `${list.length} PayPal invoice${list.length === 1 ? "" : "s"} sent${failed ? `, ${failed} failed` : ""}. This page updates as people pay.`;
  document.querySelector(".check").classList.toggle("demo", demo && !done);
}

function statusChip(r) {
  if (!r.ok) return `<span class="status err">Failed</span>`;
  const s = state.tracker.statuses[r.invoice_id];
  if (!s) return `<span class="status demo">Checking…</span>`;
  if (s.state === "paid") return `<span class="status paid">Paid ✓</span>`;
  if (s.state === "partial") return `<span class="status wait">Part paid</span>`;
  if (s.state === "cancelled") return `<span class="status err">Cancelled</span>`;
  if (s.state === "unknown") return `<span class="status err" title="${esc(s.error || "")}">Unknown</span>`;
  return `<span class="status wait">Waiting</span>`;
}

function renderTracker() {
  pendingRender = false;
  const t = state.tracker;
  renderTrackerHeader();
  $("results").innerHTML = t.results.map((r) => {
    const s = t.statuses[r.invoice_id] || {};
    const paid = s.state === "paid";
    const canRemind = r.ok && s.state && !paid && s.state !== "cancelled";
    const d = t.drafts[r.invoice_id];
    const open = t.open === r.invoice_id;
    return `
    <div class="result-wrap">
      <div class="result ${paid ? "is-paid" : ""}">
        <div class="avatar">${paid ? "✓" : esc(r.name.charAt(0).toUpperCase())}</div>
        <div>
          <div class="person-name">${esc(r.name)} ${statusChip(r)}</div>
          <div class="person-sub">${esc(r.email || "")}</div>
          ${r.ok ? "" : `<div class="err-text">${esc(r.error)}</div>`}
        </div>
        <div class="amount">${money(r.total_cents, t.currency)}
          ${r.pay_url ? `<div><a href="${esc(r.pay_url)}" target="_blank" rel="noopener">View invoice ↗</a></div>` : ""}
          ${r.invoice_number || r.invoice_id ? `<div class="person-sub">#${esc(r.invoice_number || r.invoice_id)}</div>` : ""}
        </div>
      </div>
      ${canRemind && !open ? `
      <div class="result-actions">
        <button class="btn-sm" data-remind="${esc(r.invoice_id)}" type="button">🔔 Remind</button>
        ${r.simulated ? `<button class="btn-sm" data-demopay="${esc(r.invoice_id)}" type="button">Simulate payment</button>` : ""}
        ${s.reminders ? `<span class="reminded">Reminded ${s.reminders}×</span>` : ""}
      </div>` : ""}
      ${open ? `
      <div class="remind-panel" data-panel="${esc(r.invoice_id)}">
        ${!d ? `<p class="muted small"><span class="spinner dark"></span> Writing a reminder with AI…</p>` : `
        <span class="tag">AI draft · reminder ${d.number}</span>
        <input type="text" data-subject="${esc(r.invoice_id)}" value="${esc(d.subject)}" aria-label="Reminder subject">
        <textarea data-note="${esc(r.invoice_id)}" aria-label="Reminder message">${esc(d.note)}</textarea>
        <div class="btn-row">
          <button class="btn-sm" data-cancel="${esc(r.invoice_id)}" type="button">Cancel</button>
          <button class="btn-sm" data-redraft="${esc(r.invoice_id)}" type="button">↻ Rewrite</button>
          <button class="btn-sm solid" data-sendremind="${esc(r.invoice_id)}" type="button">Send reminder</button>
        </div>`}
      </div>` : ""}
    </div>`;
  }).join("");
}

async function draftReminder(id) {
  const t = state.tracker;
  const r = t.results.find((x) => x.invoice_id === id);
  t.open = id;
  delete t.drafts[id];
  renderTracker();
  try {
    t.drafts[id] = await api("/api/reminder/draft", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        invoice_id: id, name: r.name, payer: t.payer || "Me", amount: money(r.total_cents, t.currency),
        merchant: t.merchant || "", items: r.items || [], minutes_since_sent: (Date.now() - t.sentAt) / 60000,
      }),
    });
  } catch (e) {
    toast(e.message, true);
    t.open = null;
  }
  renderTracker();
}

$("results").addEventListener("input", (e) => {
  const t = state.tracker;
  const id = e.target.dataset.note || e.target.dataset.subject;
  if (!id || !t.drafts[id]) return;
  t.drafts[id][e.target.dataset.note ? "note" : "subject"] = e.target.value;
});

$("results").addEventListener("focusout", () => {
  setTimeout(() => {
    if (pendingRender && !document.activeElement?.closest?.(".remind-panel")) renderTracker();
  }, 0);
});

$("results").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  const t = state.tracker;
  const d = b.dataset;
  if (d.remind || d.redraft) return draftReminder(d.remind || d.redraft);
  if (d.cancel) { t.open = null; return renderTracker(); }
  if (d.demopay) {
    try {
      await api("/api/demo/pay", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ invoice_id: d.demopay }) });
      await pollStatuses();
    } catch (err) { toast(err.message, true); }
    return;
  }
  if (d.sendremind) {
    const id = d.sendremind;
    const draft = t.drafts[id];
    const r = t.results.find((x) => x.invoice_id === id);
    b.disabled = true;
    b.textContent = "Sending…";
    try {
      const res = await api("/api/reminder/send", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ invoice_id: id, subject: draft.subject, note: draft.note }),
      });
      if (t.statuses[id]) t.statuses[id].reminders = res.reminders;
      t.open = null;
      delete t.drafts[id];
      saveTracker();
      renderTracker();
      toast(res.simulated ? `Reminder to ${r.name} recorded (demo, not sent).` : `Reminder sent to ${r.name} via PayPal.`);
    } catch (err) {
      toast(err.message, true);
      b.disabled = false;
      b.textContent = "Send reminder";
    }
  }
});

$("btnRefresh").onclick = () => {
  if (!pollTimer && !allPaid()) pollTimer = setInterval(pollStatuses, POLL_MS);
  pollStatuses();
};

$("btnResume").onclick = () => {
  const saved = loadSavedTracker();
  if (!saved) return $("btnResume").classList.add("hidden");
  startTracking(saved);
  goTo(4);
};

$("btnRestart").onclick = () => {
  stopTracking();
  $("btnResume").classList.toggle("hidden", !loadSavedTracker());
  state.receipt = null; state.split = null;
  $("description").value = ""; $("note").value = "";
  $("receiptCard").classList.add("hidden");
  $("scanCard").classList.remove("hidden");
  $("dropzone").classList.remove("hidden");
  goTo(1);
};

loadConfig();
$("btnResume").classList.toggle("hidden", !loadSavedTracker());
goTo(1);
