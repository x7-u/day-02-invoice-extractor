/* Day 02 -- Invoice Data Extractor
   Vanilla JS · drag-and-drop · CSRF · AbortController · multi-file queue. */

(() => {
  "use strict";
  const $  = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

  // --- refs --------------------------------------------------------------
  const apiKeyInput  = $("#api-key");
  const skipAi       = $("#skip-ai");
  const modelSel     = $("#model");
  const extractBtn   = $("#extract-btn");
  const resetBtn     = $("#reset");
  const themeBtn     = $("#theme-toggle");
  const radios       = $$('input[name="src"]');
  const dropzone     = $("#upload-zone");
  const fileInput    = $("#file-input");
  const fileListEl   = $("#file-list");
  const bundledZone  = $("#bundled-zone");

  const empty   = $("#empty-state");
  const loading = $("#loading-state");
  const errorEl = $("#error-state");
  const errorMsg= $("#error-message");
  const loadMsg = $("#loading-message");
  const resultsEl = $("#results");
  const summaryEl = $("#results-summary");
  const listEl    = $("#invoice-list");
  const exportEl  = $("#export-strip");
  const csvLink   = $("#csv-link");
  const xlsxLink  = $("#xlsx-link");

  const ledgerSizeLine = $("#ledger-size-line");
  const ledgerToggle   = $("#ledger-toggle");
  const ledgerPanel    = $("#ledger-panel");
  const ledgerRefresh  = $("#ledger-refresh");
  const ledgerClear    = $("#ledger-clear");
  const ledgerClose    = $("#ledger-close");
  const ledgerBody     = $("#ledger-body");

  const statInvoices = $("#stat-invoices");
  const statCost     = $("#stat-cost");
  const statDupes    = $("#stat-dupes");
  const statLedger   = $("#stat-ledger");

  // --- state -------------------------------------------------------------
  let queued = [];          // { name, file }
  let inFlight = null;
  const STORAGE_KEY = "day02.lastResult.v1";

  // --- CSRF + theme ------------------------------------------------------
  function readCookie(name) {
    return document.cookie.split(";").map(c => c.trim()).find(c => c.startsWith(name + "="))
      ?.slice(name.length + 1) || "";
  }
  function applyTheme(t) {
    document.documentElement.setAttribute("data-theme", t);
    try { localStorage.setItem("day02.theme", t); } catch (_) {}
  }
  (function initTheme() {
    let t;
    try { t = localStorage.getItem("day02.theme"); } catch (_) {}
    if (!t) t = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    applyTheme(t);
  })();
  themeBtn.addEventListener("click", () => {
    const cur = document.documentElement.getAttribute("data-theme") || "light";
    applyTheme(cur === "dark" ? "light" : "dark");
  });

  // --- source toggle -----------------------------------------------------
  function syncSourceUI() {
    const v = (radios.find(r => r.checked) || radios[0]).value;
    dropzone.hidden    = v !== "upload";
    bundledZone.hidden = v !== "bundled";
  }
  radios.forEach(r => r.addEventListener("change", syncSourceUI));
  syncSourceUI();

  // --- bundled-sample bulk toggles -------------------------------------
  function setAllSampleChecks(checked) {
    $$(".sample-check").forEach(c => { c.checked = checked; });
  }
  const samplesAllBtn  = $("#samples-all");
  const samplesNoneBtn = $("#samples-none");
  if (samplesAllBtn)  samplesAllBtn.addEventListener("click",  () => setAllSampleChecks(true));
  if (samplesNoneBtn) samplesNoneBtn.addEventListener("click", () => setAllSampleChecks(false));

  // --- drag and drop -----------------------------------------------------
  function addFiles(files) {
    for (const f of files) {
      // Prevent duplicates by (name + size)
      if (queued.some(q => q.file.name === f.name && q.file.size === f.size)) continue;
      queued.push({ name: f.name, file: f });
    }
    renderQueue();
  }
  function renderQueue() {
    fileListEl.innerHTML = "";
    queued.forEach((q, i) => {
      const li = document.createElement("li");
      li.innerHTML = `<span>${escapeHtml(q.file.name)} <span style="color:var(--muted)">(${humanBytes(q.file.size)})</span></span>
                      <span class="remove-x" data-i="${i}" title="Remove">✕</span>`;
      fileListEl.appendChild(li);
    });
    fileListEl.querySelectorAll(".remove-x").forEach(x => {
      x.addEventListener("click", (e) => {
        e.stopPropagation();
        const i = parseInt(e.target.dataset.i, 10);
        queued.splice(i, 1);
        renderQueue();
      });
    });
  }
  dropzone.addEventListener("click", (e) => {
    if (e.target.closest(".remove-x")) return;
    fileInput.click();
  });
  dropzone.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
  });
  fileInput.addEventListener("change", () => addFiles(fileInput.files || []));
  ["dragenter","dragover"].forEach(ev => dropzone.addEventListener(ev, (e) => {
    e.preventDefault(); dropzone.classList.add("drag-over");
  }));
  ["dragleave","drop"].forEach(ev => dropzone.addEventListener(ev, (e) => {
    e.preventDefault(); dropzone.classList.remove("drag-over");
  }));
  dropzone.addEventListener("drop", (e) => {
    if (e.dataTransfer && e.dataTransfer.files) addFiles(e.dataTransfer.files);
  });

  // --- page-wide drag overlay ------------------------------------------
  // Drop anywhere on the window -- we surface a full-page target and route
  // dropped files into the queue. A counter handles the dragenter/leave
  // bubbling problem (events fire on every child element).
  const pageDropOverlay = $("#page-drop-overlay");
  let dragCounter = 0;
  function dragHasFiles(e) {
    const t = e.dataTransfer;
    if (!t) return false;
    if (t.types) {
      for (const ty of t.types) if (ty === "Files" || ty === "application/x-moz-file") return true;
    }
    return false;
  }
  window.addEventListener("dragenter", (e) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    dragCounter += 1;
    if (pageDropOverlay) pageDropOverlay.hidden = false;
  });
  window.addEventListener("dragover", (e) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    if (e.dataTransfer) e.dataTransfer.dropEffect = "copy";
  });
  window.addEventListener("dragleave", (e) => {
    if (!dragHasFiles(e)) return;
    dragCounter = Math.max(0, dragCounter - 1);
    if (dragCounter === 0 && pageDropOverlay) pageDropOverlay.hidden = true;
  });
  window.addEventListener("drop", (e) => {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    dragCounter = 0;
    if (pageDropOverlay) pageDropOverlay.hidden = true;
    const files = e.dataTransfer && e.dataTransfer.files;
    if (!files || !files.length) return;
    // Drop forces source = upload, so files don't get silently swallowed
    // when the user is on the bundled-samples tab.
    const uploadRadio = radios.find(r => r.value === "upload");
    if (uploadRadio && !uploadRadio.checked) {
      uploadRadio.checked = true;
      syncSourceUI();
    }
    addFiles(files);
    dropzone.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });

  // --- paste from clipboard --------------------------------------------
  // Press Ctrl/Cmd+V anywhere on the page to attach a copied screenshot
  // (e.g. snipped from an email) without saving it to disk first.
  window.addEventListener("paste", (e) => {
    if (!e.clipboardData) return;
    // Skip if the user is pasting into a real text input.
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    const items = e.clipboardData.items || [];
    const files = [];
    for (const it of items) {
      if (it.kind === "file") {
        const f = it.getAsFile();
        if (f) {
          // Clipboard images are usually called "image.png" -- make it unique.
          const ts = new Date().toISOString().replace(/[:.]/g, "-");
          const ext = (f.type.split("/")[1] || "png").replace(/[^a-z0-9]/gi, "");
          const renamed = new File([f], `pasted-${ts}.${ext}`, { type: f.type });
          files.push(renamed);
        }
      }
    }
    if (files.length === 0) return;
    e.preventDefault();
    const uploadRadio = radios.find(r => r.value === "upload");
    if (uploadRadio && !uploadRadio.checked) {
      uploadRadio.checked = true;
      syncSourceUI();
    }
    addFiles(files);
    dropzone.scrollIntoView({ behavior: "smooth", block: "nearest" });
  });

  // --- states ------------------------------------------------------------
  function showState(name) {
    empty.hidden    = name !== "empty";
    loading.hidden  = name !== "loading";
    errorEl.hidden  = name !== "error";
    resultsEl.hidden= name !== "results";
    if (name === "error") setTimeout(() => errorEl.focus(), 0);
  }
  function showError(msg) { errorMsg.textContent = msg; showState("error"); }

  // --- extract -----------------------------------------------------------
  extractBtn.addEventListener("click", () => extract().catch(e => showError(e.message)));
  resetBtn.addEventListener("click", () => {
    try { localStorage.removeItem(STORAGE_KEY); } catch (_) {}
    queued = []; renderQueue();
    document.title = "Day 02 · Invoice Workbench";
    resetBtn.hidden = true;
    showState("empty");
    if (statInvoices) {
      statInvoices.textContent = "n/a";
      statCost.textContent = "n/a";
      statDupes.textContent = "n/a";
    }
  });
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      if (!extractBtn.disabled) extractBtn.click();
    }
  });

  async function extract() {
    const v = (radios.find(r => r.checked) || radios[0]).value;
    if (inFlight) inFlight.abort();
    const ac = new AbortController();
    inFlight = ac;

    const fd = new FormData();
    fd.append("skip_ai", skipAi.checked ? "true" : "false");
    if (apiKeyInput.value.trim()) fd.append("api_key", apiKeyInput.value.trim());
    if (modelSel.value)            fd.append("model", modelSel.value);

    if (v === "bundled") {
      fd.append("use_samples", "true");
      $$(".sample-check:checked").forEach(c => fd.append("sample_ids", c.value));
    } else {
      if (queued.length === 0) return showError("Add one or more files first.");
      fd.append("use_samples", "false");
      for (const q of queued) fd.append("files", q.file, q.name);
    }

    extractBtn.disabled = true;
    loadMsg.textContent = skipAi.checked
      ? "Loading files (AI skipped)…"
      : `Extracting via ${modelSel.value || "Haiku 4.5"} (~5-15s per page)…`;
    showState("loading");

    let resp, text;
    try {
      const csrf = readCookie("csrf_token");
      resp = await fetch("/api/extract", {
        method: "POST", body: fd, signal: ac.signal,
        headers: csrf ? { "X-CSRF-Token": csrf } : {},
      });
      text = await resp.text();
    } catch (e) {
      extractBtn.disabled = false;
      if (e.name === "AbortError") return;
      return showError(`Network error: ${e.message}. Is the server running? Run start.bat or 'python server.py'.`);
    } finally {
      inFlight = null;
    }

    extractBtn.disabled = false;

    let data;
    try { data = JSON.parse(text); }
    catch {
      const ctype = resp.headers.get("content-type") || "unknown";
      const port = location.port || "1002";
      return showError(`Server returned ${resp.status} (${ctype}). Likely a stale browser tab -- close other localhost:${port} tabs, restart with start.bat, and try again.\nPreview: ${text.slice(0, 240)}`);
    }
    if (!resp.ok || data.error) return showError(data.error || `HTTP ${resp.status}`);

    render(data);
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ at: Date.now(), data })); } catch (_) {}
    showState("results");
    resetBtn.hidden = false;
  }

  // --- render ------------------------------------------------------------
  function render(data) {
    document.title = `${data.count} invoice${data.count===1?"":"s"} · Day 02`;
    summaryEl.innerHTML = renderKpis(data);
    listEl.innerHTML = data.results.map(renderInvoice).join("");
    listEl.querySelectorAll(".line-items-toggle").forEach(btn => {
      btn.addEventListener("click", () => {
        const i = btn.dataset.idx;
        const panel = listEl.querySelector(`.line-items[data-idx="${i}"]`);
        const open = !panel.hidden;
        panel.hidden = open;
        btn.textContent = open ? "▾ Show line items" : "▴ Hide line items";
      });
    });
    if (data.csv_filename || data.xlsx_filename) {
      exportEl.hidden = false;
      csvLink.href  = data.csv_filename  ? `/api/download/${encodeURIComponent(data.csv_filename)}`  : "#";
      xlsxLink.href = data.xlsx_filename ? `/api/download/${encodeURIComponent(data.xlsx_filename)}` : "#";
      csvLink.textContent  = `⬇️ ${data.csv_filename}`;
      xlsxLink.textContent = `⬇️ ${data.xlsx_filename}`;
    } else {
      exportEl.hidden = true;
    }
    if (typeof data.ledger_size === "number") setLedgerSize(data.ledger_size);
    setHeroStats(data);
  }

  function setHeroStats(data) {
    if (!statInvoices) return;
    statInvoices.textContent = String(data.count ?? 0);
    const cost = typeof data.total_cost_usd === "number" ? data.total_cost_usd : 0;
    statCost.textContent = `$${cost.toFixed(4)}`;
    const dupes = data.duplicates_found || 0;
    statDupes.textContent = String(dupes);
    if (typeof data.ledger_size === "number") {
      statLedger.textContent = String(data.ledger_size);
    }
  }

  function renderKpis(data) {
    const counts = { high: 0, medium: 0, low: 0 };
    let totalLines = 0;
    let byCurrency = {};
    for (const r of data.results) {
      const c = (r.invoice && r.invoice.confidence) || "";
      if (counts[c] !== undefined) counts[c] += 1;
      const lines = (r.invoice && r.invoice.line_items) || [];
      totalLines += lines.length;
      const cur = (r.invoice && r.invoice.currency) || "";
      const tot = (r.invoice && r.invoice.total_amount) || 0;
      if (cur && tot) byCurrency[cur] = (byCurrency[cur] || 0) + tot;
    }
    const totalSpend = Object.entries(byCurrency).map(([k, v]) => `${k} ${v.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}`).join(" · ") || "n/a";
    const dupes = data.duplicates_found || 0;
    const dupeKpi = dupes > 0
      ? `<div class="kpi kpi-red"><h2>${dupes}</h2><p>Duplicate${dupes === 1 ? "" : "s"} flagged</p></div>`
      : "";
    return `
      <div class="kpi kpi-navy"><h2>${data.count}</h2><p>${data.count === 1 ? "invoice" : "invoices"}</p></div>
      <div class="kpi kpi-green"><h2>${counts.high}</h2><p>High confidence</p></div>
      <div class="kpi kpi-amber"><h2>${counts.medium}</h2><p>Medium</p></div>
      <div class="kpi kpi-red"><h2>${counts.low}</h2><p>Low</p></div>
      ${dupeKpi}
      <div class="kpi kpi-navy" style="flex: 2 1 0; min-width: 240px;">
        <h2 style="font-size: 1.2rem; line-height: 1.4;">${escapeHtml(totalSpend)}</h2>
        <p>Total spend · cost $${data.total_cost_usd.toFixed(4)}</p>
      </div>
    `;
  }

  function renderInvoice(r, idx) {
    const inv = r.invoice || {};
    const vendor = (inv.vendor || {}).name || "(unknown)";
    const conf = (inv.confidence || "low");
    const cur = inv.currency || "";
    const total = inv.total_amount;
    const totalStr = (total !== null && total !== undefined) ? `${cur} ${total.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2})}` : "n/a";
    const warnings = inv.extraction_warnings || [];
    const lines = inv.line_items || [];

    const meta = [
      ["Invoice #", inv.invoice_number || "n/a"],
      ["Date",      inv.invoice_date || "n/a"],
      ["Due",       inv.due_date || "n/a"],
      ["Currency",  inv.currency || "n/a"],
      ["Subtotal",  formatNum(inv.subtotal)],
      ["Tax",       formatNum(inv.tax_amount)],
      ["Total",     totalStr],
      ["Lines",     String(lines.length)],
    ];

    const linesHtml = lines.length === 0 ? "<p class='muted'>No line items extracted.</p>" : `
      <table>
        <thead><tr><th>Description</th><th>Qty</th><th>Unit price</th><th>Total</th></tr></thead>
        <tbody>${lines.map(li => `
          <tr>
            <td>${escapeHtml(li.description || "")}</td>
            <td>${li.quantity != null ? li.quantity : "n/a"}</td>
            <td>${formatNum(li.unit_price)}</td>
            <td>${formatNum(li.total)}</td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;

    const warningsHtml = warnings.length === 0 ? "" : `
      <div class="warnings-list">
        <strong>Validation warnings:</strong>
        <ul>${warnings.map(w => `<li>${escapeHtml(w)}</li>`).join("")}</ul>
      </div>`;

    const dup = r.duplicate || null;
    const dupStatus = dup && dup.status;
    const dupPill = (dupStatus && dupStatus !== "unique")
      ? `<span class="dup-pill dup-${escapeAttr(dupStatus)}" title="${escapeAttr(dup.match && dup.match.explanation || "")}">${dupStatus.toUpperCase()} DUPE</span>`
      : "";
    const dupDetail = (dupStatus && dupStatus !== "unique" && dup.match) ? `
      <div class="dup-block dup-block-${escapeAttr(dupStatus)}">
        <strong>Duplicate detected (${escapeHtml(dupStatus)})</strong>
        <p>${escapeHtml(dup.match.explanation)}</p>
        <p class="muted">Matches ledger entry from <code>${escapeHtml(dup.match.matched_filename || "?")}</code> on ${escapeHtml(dup.match.matched_extracted_at || "?")}.</p>
      </div>` : "";

    const costLine = r.cost_usd > 0
      ? `Cost: $${r.cost_usd.toFixed(4)} · ${r.input_tokens} in / ${r.output_tokens} out · ${escapeHtml(r.model)} · ${r.n_pages} page${r.n_pages===1?"":"s"}`
      : (r.skipped ? "AI skipped -- $0.00" : "");

    return `
      <article class="invoice-card">
        <div class="card-head">
          <div>
            <h3>${escapeHtml(vendor)}</h3>
            <div class="source-name">${escapeHtml(r.filename || "")}</div>
          </div>
          <div class="card-pills">
            ${dupPill}
            <span class="conf-pill conf-${escapeAttr(conf)}">${conf.toUpperCase()}</span>
          </div>
        </div>

        <div class="invoice-meta">
          ${meta.map(([k, v]) => `<div><div class="label">${escapeHtml(k)}</div><div class="value">${escapeHtml(String(v))}</div></div>`).join("")}
        </div>

        ${dupDetail}
        ${warningsHtml}

        <button type="button" class="line-items-toggle" data-idx="${idx}">▾ Show line items</button>
        <div class="line-items" data-idx="${idx}" hidden>${linesHtml}</div>

        ${costLine ? `<div class="cost-line">${escapeHtml(costLine)}</div>` : ""}
      </article>
    `;
  }

  function formatNum(n) {
    if (n == null) return "n/a";
    return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function humanBytes(n) {
    if (n < 1024) return `${n} B`;
    if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
    return `${(n / 1024 / 1024).toFixed(2)} MB`;
  }

  // --- ledger ------------------------------------------------------------
  function setLedgerSize(n) {
    if (!ledgerSizeLine) return;
    if (n === 0) {
      ledgerSizeLine.textContent = "No invoices recorded yet.";
    } else {
      ledgerSizeLine.textContent = `${n} invoice${n === 1 ? "" : "s"} on record.`;
    }
  }

  async function fetchLedger() {
    try {
      const resp = await fetch("/api/ledger");
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      renderLedger(data);
      setLedgerSize(data.size);
      if (statLedger) statLedger.textContent = String(data.size);
    } catch (e) {
      ledgerBody.innerHTML = `<p class="muted">Could not load ledger: ${escapeHtml(e.message)}</p>`;
    }
  }

  function renderLedger(data) {
    const entries = data.entries || [];
    if (entries.length === 0) {
      ledgerBody.innerHTML = `<p class="muted">Ledger is empty. Successful extractions are recorded automatically.</p>`;
      return;
    }
    ledgerBody.innerHTML = `
      <table class="ledger-table">
        <thead>
          <tr><th>When</th><th>Vendor</th><th>Invoice #</th><th>Date</th><th>Amount</th><th>Source</th><th></th></tr>
        </thead>
        <tbody>${entries.map(e => `
          <tr data-id="${escapeAttr(e.id)}">
            <td><code>${escapeHtml((e.extracted_at || "").replace("T", " ").replace("Z", ""))}</code></td>
            <td>${escapeHtml(e.vendor || "")}</td>
            <td>${escapeHtml(e.invoice_number || "n/a")}</td>
            <td>${escapeHtml(e.invoice_date || "n/a")}</td>
            <td>${escapeHtml(e.currency || "")} ${e.total_amount != null ? e.total_amount.toLocaleString(undefined, {minimumFractionDigits: 2, maximumFractionDigits: 2}) : "n/a"}</td>
            <td><code>${escapeHtml(e.filename || "")}</code></td>
            <td><button type="button" class="ledger-remove btn btn-ghost" data-id="${escapeAttr(e.id)}" title="Remove this entry">✕</button></td>
          </tr>
        `).join("")}</tbody>
      </table>
    `;
    ledgerBody.querySelectorAll(".ledger-remove").forEach(btn => {
      btn.addEventListener("click", () => removeLedgerEntry(btn.dataset.id));
    });
  }

  async function removeLedgerEntry(id) {
    if (!id) return;
    try {
      const csrf = readCookie("csrf_token");
      const resp = await fetch(`/api/ledger/${encodeURIComponent(id)}`, {
        method: "DELETE",
        headers: csrf ? { "X-CSRF-Token": csrf } : {},
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await fetchLedger();
    } catch (e) {
      alert(`Could not remove entry: ${e.message}`);
    }
  }

  async function clearLedger() {
    if (!confirm("Clear the entire ledger? Future uploads will start with no duplicate history.")) return;
    try {
      const csrf = readCookie("csrf_token");
      const resp = await fetch("/api/ledger/clear", {
        method: "POST",
        headers: csrf ? { "X-CSRF-Token": csrf } : {},
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      await fetchLedger();
    } catch (e) {
      alert(`Could not clear ledger: ${e.message}`);
    }
  }

  function toggleLedgerPanel() {
    const opening = ledgerPanel.hidden;
    ledgerPanel.hidden = !opening;
    ledgerToggle.textContent = opening ? "Hide ledger" : "View ledger";
    if (opening) {
      fetchLedger();
      ledgerPanel.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  if (ledgerToggle)  ledgerToggle.addEventListener("click", toggleLedgerPanel);
  if (ledgerClose)   ledgerClose.addEventListener("click", toggleLedgerPanel);
  if (ledgerRefresh) ledgerRefresh.addEventListener("click", fetchLedger);
  if (ledgerClear)   ledgerClear.addEventListener("click", clearLedger);

  // Prime the dock + hero ledger counters on load -- quiet, ignore failure.
  (function primeLedgerSize() {
    fetch("/api/status").then(r => r.ok ? r.json() : null).then(d => {
      if (d && typeof d.ledger_size === "number") {
        setLedgerSize(d.ledger_size);
        if (statLedger) statLedger.textContent = String(d.ledger_size);
      }
    }).catch(() => {});
  })();

  // --- restore last result ----------------------------------------------
  (function restoreLast() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return;
      const { at, data } = JSON.parse(raw);
      if (!data || Date.now() - at > 6 * 3600 * 1000) return;
      render(data);
      showState("results");
      resetBtn.hidden = false;
    } catch (_) {}
  })();

  // --- escapers ----------------------------------------------------------
  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function escapeAttr(s) { return String(s).replace(/[^a-zA-Z0-9_-]/g, ""); }
})();
