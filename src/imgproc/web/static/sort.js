/* Visual SKU sort.
   - Pick xlsx + tunables, kick a /api/sort/run job.
   - Poll status, render per-SKU rows when done.
   - Click candidate → toggle hero / extras.
   - Apply → POST mappings, server copies into processed/sorted/.

   Vanilla DOM, mirrors reviewer.js style. */

(function () {
  "use strict";

  // Pull batch name from URL: /sort/{name}
  const batchName = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop());

  const el = {
    batchName: document.getElementById("sort-batch-name"),
    summary: document.getElementById("sort-summary"),
    banner: document.getElementById("sort-banner"),
    setup: document.getElementById("sort-setup"),
    xlsxSelect: document.getElementById("sort-xlsx-select"),
    xlsxPath: document.getElementById("sort-xlsx-path"),
    runBtn: document.getElementById("sort-run-btn"),
    threshold: document.getElementById("sort-threshold"),
    loose: document.getElementById("sort-loose"),
    margin: document.getElementById("sort-margin"),
    dupe: document.getElementById("sort-dupe"),
    progress: document.getElementById("sort-progress"),
    progressText: document.getElementById("sort-progress-text"),
    progressFill: document.getElementById("sort-progress-fill"),
    results: document.getElementById("sort-results"),
    tierChips: document.getElementById("sort-tier-chips"),
    applyBtn: document.getElementById("sort-apply-btn"),
    skus: document.getElementById("sort-skus"),
    dupes: document.getElementById("sort-dupes"),
    dupesCount: document.getElementById("sort-dupes-count"),
    dupesList: document.getElementById("sort-dupes-list"),
    skuTpl: document.getElementById("sort-sku-tpl"),
    candTpl: document.getElementById("sort-candidate-tpl"),
  };

  let state = {
    jobId: null,
    skus: [],          // [{sku, tier, matches: [{filename, distance, rank}]}]
    candidates: [],
    dupes: [],
    // mappings[sku] = { hero: filename | null, extras: [filename, …] }
    mappings: {},
    activeTier: null,  // null = all
  };

  el.batchName.textContent = batchName;
  el.runBtn.addEventListener("click", onRun);
  el.applyBtn.addEventListener("click", onApply);
  el.xlsxSelect.addEventListener("change", () => {
    if (el.xlsxSelect.value) el.xlsxPath.value = el.xlsxSelect.value;
  });

  populateXlsxList();
  preloadBatchXlsx();

  async function preloadBatchXlsx() {
    // If this batch has its own working-copy xlsx, prefill the path input
    // so Alida's one click away from running the match. She can still
    // override it via the picker / paste field.
    try {
      const r = await fetch("/api/batches");
      if (!r.ok) return;
      const data = await r.json();
      const b = (data.batches || []).find(x => x.name === batchName);
      if (b && b.xlsx_filename) {
        el.xlsxPath.value = `${data.root}/${batchName}/${b.xlsx_filename}`;
        el.xlsxPath.title = "Auto-filled from this batch's attached spreadsheet";
      }
    } catch {}
  }

  async function populateXlsxList() {
    try {
      const r = await fetch("/api/sheetcheck/source-files");
      if (!r.ok) return;
      const data = await r.json();
      el.xlsxSelect.innerHTML = '<option value="">— select an xlsx from source/ —</option>' + (
        data.files || []
      ).map(f => `<option value="${escapeAttr(f.path)}">${escapeHtml(f.relative)}</option>`).join("");
    } catch (e) {}
  }

  // ── Run ──────────────────────────────────────────────────────────
  async function onRun() {
    const xlsxPath = (el.xlsxPath.value || "").trim() || el.xlsxSelect.value;
    if (!xlsxPath) {
      flashBanner("Pick a spreadsheet first.", "error");
      return;
    }
    el.runBtn.disabled = true;
    hideBanner();
    showProgress("Starting…", 0);
    try {
      const r = await fetch("/api/sort/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          xlsx_path: xlsxPath,
          batch_name: batchName,
          threshold: +el.threshold.value || 10,
          loose_threshold: +el.loose.value || 18,
          min_margin: +el.margin.value || 4,
          dupe_threshold: +el.dupe.value || 10,
        }),
      });
      const data = await r.json();
      if (!r.ok) {
        flashBanner(data.detail || "Could not start sort job.", "error");
        el.runBtn.disabled = false;
        hideProgress();
        return;
      }
      state.jobId = data.job_id;
      pollJob();
    } catch (e) {
      flashBanner("Network error: " + e.message, "error");
      el.runBtn.disabled = false;
      hideProgress();
    }
  }

  async function pollJob() {
    if (!state.jobId) return;
    try {
      const r = await fetch(`/api/sort/jobs/${state.jobId}`);
      const data = await r.json();
      if (data.status === "running") {
        const phase = data.progress.phase || "running";
        const cur = data.progress.current || 0;
        const tot = data.progress.total || 0;
        const pct = tot > 0 ? Math.round(100 * cur / tot) : 0;
        showProgress(`${phase}… ${cur}/${tot}`, pct);
        setTimeout(pollJob, 500);
        return;
      }
      if (data.status === "error") {
        flashBanner("Sort failed: " + (data.error || "unknown"), "error");
        el.runBtn.disabled = false;
        hideProgress();
        return;
      }
      // done
      hideProgress();
      el.runBtn.disabled = false;
      await loadResult();
    } catch (e) {
      flashBanner("Network error: " + e.message, "error");
      setTimeout(pollJob, 1000);
    }
  }

  async function loadResult() {
    const r = await fetch(`/api/sort/result/${state.jobId}`);
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      flashBanner(data.detail || "Could not load result.", "error");
      return;
    }
    const data = await r.json();
    state.skus = data.skus || [];
    state.candidates = data.candidates || [];
    state.dupes = data.dupes || [];
    state.mappings = {};
    // Auto-pick the rank-0 candidate for every strict-tier SKU as a starting point —
    // Alida can override anything. Margin/weak left blank so the user has to look.
    for (const s of state.skus) {
      if (s.tier === "strict" && s.matches.length) {
        state.mappings[s.sku] = { hero: s.matches[0].filename, extras: [] };
      } else {
        state.mappings[s.sku] = { hero: null, extras: [] };
      }
    }
    renderSummary();
    renderTierChips();
    renderDupes();
    renderSkus();
    el.results.hidden = false;
  }

  // ── Render helpers ───────────────────────────────────────────────
  function renderSummary() {
    const total = state.skus.length;
    const strict = state.skus.filter(s => s.tier === "strict").length;
    const margin = state.skus.filter(s => s.tier === "margin").length;
    const weak = state.skus.filter(s => s.tier === "weak").length;
    el.summary.innerHTML =
      `<span><b>${total}</b> SKUs</span>` +
      `<span><b>${strict}</b> strict</span>` +
      `<span><b>${margin}</b> margin</span>` +
      `<span><b>${weak}</b> weak</span>` +
      `<span><b>${state.candidates.length}</b> photos</span>`;
  }

  function renderTierChips() {
    const counts = { all: state.skus.length };
    for (const s of state.skus) counts[s.tier] = (counts[s.tier] || 0) + 1;
    el.tierChips.innerHTML = "";
    el.tierChips.appendChild(makeTierChip("All", null, counts.all));
    for (const tier of ["strict", "margin", "weak"]) {
      el.tierChips.appendChild(makeTierChip(cap(tier), tier, counts[tier] || 0));
    }
  }
  function makeTierChip(label, tier, count) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "sort-chip" + (state.activeTier === tier ? " active" : "");
    b.innerHTML = `${label} <span class="count">${count}</span>`;
    b.addEventListener("click", () => {
      state.activeTier = (state.activeTier === tier) ? null : tier;
      renderTierChips();
      renderSkus();
    });
    return b;
  }

  function renderDupes() {
    if (!state.dupes.length) {
      el.dupes.hidden = true;
      return;
    }
    el.dupes.hidden = false;
    el.dupesCount.textContent = `(${state.dupes.length})`;
    el.dupesList.innerHTML = "";
    for (const cluster of state.dupes) {
      const wrap = document.createElement("div");
      wrap.className = "sort-dupe-cluster";
      cluster.files.forEach((f, i) => {
        const t = document.createElement("div");
        t.className = "sort-dupe-thumb";
        t.title = `${f} (Hamming distance ${cluster.distances[i]})`;
        const img = document.createElement("img");
        img.src = thumbUrl(f);
        img.loading = "lazy";
        const lbl = document.createElement("span");
        lbl.className = "label";
        lbl.textContent = f;
        t.appendChild(img);
        t.appendChild(lbl);
        wrap.appendChild(t);
      });
      el.dupesList.appendChild(wrap);
    }
  }

  function renderSkus() {
    el.skus.innerHTML = "";
    const list = state.activeTier
      ? state.skus.filter(s => s.tier === state.activeTier)
      : state.skus;
    for (const s of list) {
      el.skus.appendChild(renderSkuRow(s));
    }
  }

  function renderSkuRow(s) {
    const node = el.skuTpl.content.cloneNode(true);
    const li = node.querySelector(".sort-sku-row");
    li.classList.add(`tier-${s.tier || "weak"}`);

    li.querySelector(".sort-anchor-img").src = `/api/sort/anchor/${state.jobId}/${encodeURIComponent(s.sku)}`;
    li.querySelector(".sort-anchor-img").alt = s.sku;
    li.querySelector(".sort-anchor-sku").textContent = s.sku;
    li.querySelector(".sort-anchor-tier").textContent = s.tier || "weak";

    const grid = li.querySelector(".sort-candidate-grid");
    if (!s.matches.length) {
      const note = document.createElement("div");
      note.style.gridColumn = "1 / -1";
      note.style.color = "var(--muted)";
      note.style.fontStyle = "italic";
      note.style.padding = "20px 8px";
      note.textContent = "No candidate matches.";
      grid.appendChild(note);
      return node;
    }
    for (const m of s.matches) {
      grid.appendChild(renderCandidate(s.sku, m));
    }
    return node;
  }

  function renderCandidate(sku, m) {
    const node = el.candTpl.content.cloneNode(true);
    const btn = node.querySelector(".sort-candidate");
    btn.querySelector("img").src = thumbUrl(m.filename);
    btn.querySelector("img").loading = "lazy";
    btn.querySelector(".sort-candidate-name").textContent = m.filename;
    btn.querySelector(".sort-candidate-name").title = m.filename;
    btn.querySelector(".sort-candidate-distance").textContent = `d${m.distance}`;

    const mapping = state.mappings[sku] || { hero: null, extras: [] };
    if (mapping.hero === m.filename) {
      btn.classList.add("hero");
    } else {
      const extraIdx = mapping.extras.indexOf(m.filename);
      if (extraIdx >= 0) {
        btn.classList.add("extra");
        btn.dataset.extraLetter = "·" + "bcdefghij"[extraIdx]?.toUpperCase();
      }
    }
    btn.addEventListener("click", (e) => onCandidateClick(sku, m.filename, e));
    return node;
  }

  // ── Click semantics ──────────────────────────────────────────────
  // Plain click  → set hero (if not hero), or clear hero.
  // Shift-click  → toggle as extra.
  function onCandidateClick(sku, filename, ev) {
    const mapping = state.mappings[sku] || { hero: null, extras: [] };
    if (ev.shiftKey) {
      const idx = mapping.extras.indexOf(filename);
      if (idx >= 0) mapping.extras.splice(idx, 1);
      else if (mapping.hero !== filename) mapping.extras.push(filename);
    } else {
      if (mapping.hero === filename) {
        mapping.hero = null;
      } else {
        // Promote from extras if it was there.
        const idx = mapping.extras.indexOf(filename);
        if (idx >= 0) mapping.extras.splice(idx, 1);
        mapping.hero = filename;
      }
    }
    state.mappings[sku] = mapping;
    renderSkus();
  }

  // ── Apply ────────────────────────────────────────────────────────
  async function onApply() {
    const mappings = state.skus
      .map(s => ({
        sku: s.sku,
        hero: state.mappings[s.sku]?.hero || null,
        extras: state.mappings[s.sku]?.extras || [],
      }))
      .filter(m => m.hero); // skip SKUs with no hero
    if (!mappings.length) {
      flashBanner("No heroes selected — nothing to apply.", "error");
      return;
    }
    el.applyBtn.disabled = true;
    try {
      const r = await fetch("/api/sort/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id: state.jobId,
          mappings,
          overwrite: true,
        }),
      });
      const data = await r.json();
      if (!r.ok) {
        flashBanner(data.detail || "Apply failed.", "error");
        return;
      }
      const summary = `Wrote ${data.written.length} files to ${shortPath(data.out_dir)}` +
        (data.skipped.length ? ` · ${data.skipped.length} skipped` : "") +
        (data.errors.length ? ` · ${data.errors.length} errors` : "");
      flashBanner(summary, data.errors.length ? "error" : "");
    } catch (e) {
      flashBanner("Network error: " + e.message, "error");
    } finally {
      el.applyBtn.disabled = false;
    }
  }

  // ── Helpers ──────────────────────────────────────────────────────
  function thumbUrl(filename) {
    return `/api/batches/${encodeURIComponent(batchName)}/thumbs/${encodeURIComponent(filename)}?w=180`;
  }

  function showProgress(text, pct) {
    el.progress.hidden = false;
    el.progressText.textContent = text;
    el.progressFill.style.width = `${pct}%`;
  }
  function hideProgress() { el.progress.hidden = true; }

  function setBanner(msg, kind) {
    el.banner.textContent = msg;
    el.banner.hidden = false;
    el.banner.className = "sort-banner" + (kind === "error" ? " error" : "");
  }
  function hideBanner() { el.banner.hidden = true; }
  function flashBanner(msg, kind) {
    setBanner(msg, kind);
    setTimeout(() => { if (el.banner.textContent === msg) hideBanner(); }, 4500);
  }

  function cap(s) { return s.charAt(0).toUpperCase() + s.slice(1); }
  function shortPath(p) {
    return (p || "").split(/[\\/]/).slice(-3).join("/");
  }
  function escapeHtml(s) {
    return String(s).replace(/[&<>]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"})[c]);
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/"/g, "&quot;");
  }
})();
