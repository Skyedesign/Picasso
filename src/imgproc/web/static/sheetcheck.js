/* Sheet check UI.
   - Picks an xlsx (from source/ list or pasted path).
   - POSTs to /api/sheetcheck/run, renders findings.
   - Filter chips per rule + a "show muted" toggle.
   - Per-finding mute / per-rule mute persists via /api/sheetcheck/suppress.

   No framework — vanilla DOM. Mirrors the reviewer's style. */

(function () {
  "use strict";

  // ── Element refs ─────────────────────────────────────────────────
  const el = {
    sourceRoot: document.getElementById("sc-source-root"),
    sourceSelect: document.getElementById("sc-source-select"),
    pathInput: document.getElementById("sc-path-input"),
    runBtn: document.getElementById("sc-run"),
    rerunBtn: document.getElementById("sc-rerun"),
    editCopyBtn: document.getElementById("sc-edit-copy"),
    saveBackBtn: document.getElementById("sc-save-back"),
    readonlyHint: document.getElementById("sc-readonly-hint"),
    summary: document.getElementById("sc-summary"),
    results: document.getElementById("sc-results"),
    fileName: document.getElementById("sc-file-name"),
    fileMeta: document.getElementById("sc-file-meta"),
    banner: document.getElementById("sc-banner"),
    ruleChips: document.getElementById("sc-rule-chips"),
    showMuted: document.getElementById("sc-show-muted"),
    findings: document.getElementById("sc-findings"),
    empty: document.getElementById("sc-empty"),
    findingTpl: document.getElementById("sc-finding-tpl"),
  };

  // ── State ────────────────────────────────────────────────────────
  let state = {
    xlsxPath: null,
    writable: false,       // true iff xlsx is inside a batch or scratch (Apply allowed)
    isScratch: false,      // true iff xlsx is a scratch working copy (Save-back available)
    scratchOrigin: null,   // for scratch copies: the source path to save back to
    findings: [],          // visible (not currently muted)
    suppressed: [],        // muted by sidecar
    mutedRules: new Set(), // rules muted file-wide
    mutedFindings: new Set(), // per-finding suppression keys
    activeRule: null,      // null = "All"
  };

  // ── Bootstrap ────────────────────────────────────────────────────
  populateSourceList();
  // Auto-load if a batch param is present (?batch=name) — the batches list
  // links here once a batch has an xlsx attached, and we want to skip the
  // picker dance in that case.
  const params = new URLSearchParams(location.search);
  const presetBatch = params.get("batch");
  if (presetBatch) {
    autoLoadFromBatch(presetBatch);
  }
  el.runBtn.addEventListener("click", onRun);
  el.rerunBtn.addEventListener("click", onRun);
  el.editCopyBtn.addEventListener("click", onMakeEditableCopy);
  el.saveBackBtn.addEventListener("click", onSaveBackToSource);
  el.pathInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); onRun(); }
  });
  el.sourceSelect.addEventListener("change", () => {
    if (el.sourceSelect.value) el.pathInput.value = el.sourceSelect.value;
  });
  el.showMuted.addEventListener("change", renderFindings);

  async function autoLoadFromBatch(batch) {
    try {
      const r = await fetch("/api/batches");
      if (!r.ok) return;
      const data = await r.json();
      const b = (data.batches || []).find(x => x.name === batch);
      if (!b || !b.xlsx_filename) {
        flashBanner(`Batch "${batch}" has no spreadsheet attached.`, "error");
        return;
      }
      // Resolve via known root (BATCHES_ROOT is exposed in the list response).
      el.pathInput.value = `${data.root}/${batch}/${b.xlsx_filename}`;
      onRun();
    } catch (e) {}
  }

  async function populateSourceList() {
    try {
      const r = await fetch("/api/sheetcheck/source-files");
      if (!r.ok) return;
      const data = await r.json();
      el.sourceRoot.textContent = data.root || "source/";
      if (!data.exists) {
        el.sourceSelect.innerHTML = '<option value="">— source/ folder not found —</option>';
        el.sourceSelect.disabled = true;
        return;
      }
      el.sourceSelect.innerHTML = '<option value="">— select an xlsx —</option>' + (
        data.files || []
      ).map(f => `<option value="${escapeAttr(f.path)}">${escapeHtml(f.relative)} (${f.size_kb} KB)</option>`).join("");
    } catch (e) {
      // Non-fatal — the user can still paste a path.
    }
  }

  // ── Run check ────────────────────────────────────────────────────
  async function onRun() {
    const path = (el.pathInput.value || "").trim() || el.sourceSelect.value;
    if (!path) {
      flashBanner("Pick a file or paste a path first.", "error");
      return;
    }
    el.runBtn.disabled = true;
    el.rerunBtn.disabled = true;
    setBanner("Running…", "info");
    try {
      const r = await fetch("/api/sheetcheck/run", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ xlsx_path: path }),
      });
      const data = await r.json();
      if (!r.ok) {
        setBanner(data.detail || "Could not run check.", "error");
        return;
      }
      ingestResult(data);
      hideBanner();
    } catch (e) {
      setBanner("Network error: " + e.message, "error");
    } finally {
      el.runBtn.disabled = false;
      el.rerunBtn.disabled = false;
    }
  }

  function ingestResult(data) {
    state.xlsxPath = data.xlsx_path;
    state.writable = !!data.writable;
    state.isScratch = !!data.is_scratch;
    state.scratchOrigin = data.scratch_origin || null;
    state.findings = data.findings || [];
    state.suppressed = data.suppressed || [];
    state.mutedRules = new Set(data.muted_rules || []);
    state.mutedFindings = new Set(data.muted_findings || []);
    state.activeRule = null;
    // Show "Make editable copy" only for read-only paths;
    // "Save back to source" only for scratch copies.
    el.editCopyBtn.hidden = state.writable;
    el.saveBackBtn.hidden = !state.isScratch;
    el.readonlyHint.hidden = state.writable;

    el.results.hidden = false;
    el.fileName.textContent = filenameOf(data.xlsx_path);
    const parts = [
      `Sheet: ${data.sheet_name}`,
      `Header row ${data.header_row}`,
      `${data.n_variants} variants`,
      `${data.n_images} images`,
      `${data.suffix_count} suffixes loaded`,
    ];
    el.fileMeta.textContent = parts.join("  ·  ");

    if (data.parse_warnings && data.parse_warnings.length) {
      setBanner("Parse warnings: " + data.parse_warnings.join("; "), "warn");
    }

    el.summary.innerHTML = `<span><b>${state.findings.length}</b> issues</span>` +
      (state.suppressed.length ? `<span>${state.suppressed.length} muted</span>` : "");

    renderRuleChips();
    renderFindings();
  }

  // ── Rule chips (filter) ──────────────────────────────────────────
  function renderRuleChips() {
    const all = [...state.findings, ...state.suppressed];
    const counts = {};
    for (const f of all) counts[f.rule] = (counts[f.rule] || 0) + 1;
    const rules = Object.keys(counts).sort();
    const total = all.length;
    el.ruleChips.innerHTML = "";
    el.ruleChips.appendChild(makeChip("All", null, total, state.activeRule === null, false));
    for (const rule of rules) {
      const muted = state.mutedRules.has(rule);
      const active = state.activeRule === rule;
      el.ruleChips.appendChild(makeChip(prettyRule(rule), rule, counts[rule], active, muted));
    }
  }

  function makeChip(label, rule, count, active, mutedRule) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "sc-chip" + (active ? " active" : "") + (mutedRule ? " muted-rule" : "");
    b.innerHTML = `${escapeHtml(label)} <span class="count">${count}</span>`;
    b.addEventListener("click", () => {
      state.activeRule = (state.activeRule === rule) ? null : rule;
      renderRuleChips();
      renderFindings();
    });
    return b;
  }

  // ── Findings list ────────────────────────────────────────────────
  function renderFindings() {
    el.findings.innerHTML = "";
    const showMuted = el.showMuted.checked;
    const visible = state.findings.filter(matchesActiveRule);
    const mutedShown = showMuted ? state.suppressed.filter(matchesActiveRule) : [];
    const all = [...visible, ...mutedShown];
    if (all.length === 0) {
      el.empty.hidden = false;
      return;
    }
    el.empty.hidden = true;
    for (const f of all) {
      const isMuted = state.mutedFindings.has(f.suppression_key) || state.mutedRules.has(f.rule);
      el.findings.appendChild(renderFinding(f, isMuted));
    }
  }

  function matchesActiveRule(f) {
    return state.activeRule === null || f.rule === state.activeRule;
  }

  function renderFinding(f, isMuted) {
    const node = el.findingTpl.content.cloneNode(true);
    const li = node.querySelector(".sc-finding");
    if (isMuted) li.classList.add("muted");

    li.querySelector(".sc-severity").textContent = f.severity;
    li.querySelector(".sc-severity").classList.add(f.severity);
    li.querySelector(".sc-rule").textContent = prettyRule(f.rule);
    li.querySelector(".sc-row").textContent = f.row ? `row ${f.row}` : "—";
    const skuEl = li.querySelector(".sc-sku");
    if (f.sku) skuEl.textContent = f.sku;
    else skuEl.hidden = true;
    li.querySelector(".sc-message").textContent = f.message;
    if (f.suggestion) {
      const s = li.querySelector(".sc-suggestion");
      s.hidden = false;
      s.innerHTML = highlightCode(f.suggestion);
    }

    const applyBtn = li.querySelector(".sc-apply-btn");
    if (f.fix && state.writable && !isMuted) {
      applyBtn.hidden = false;
      applyBtn.title = `Write "${f.fix.value}" into row ${f.fix.row} (column ${f.fix.column})`;
      applyBtn.addEventListener("click", () => applyFix(f));
    }

    const muteBtn = li.querySelector(".sc-mute-btn");
    muteBtn.textContent = isMuted ? "Unmute" : "Mute";
    muteBtn.addEventListener("click", () => toggleMute("finding", f.suppression_key, !isMuted));

    const muteRuleBtn = li.querySelector(".sc-mute-rule-btn");
    const isRuleMuted = state.mutedRules.has(f.rule);
    muteRuleBtn.textContent = isRuleMuted ? "Unmute rule" : "Mute rule";
    muteRuleBtn.addEventListener("click", () => toggleMute("rule", f.rule, !isRuleMuted));

    return node;
  }

  // ── Apply fix ────────────────────────────────────────────────────
  async function applyFix(f) {
    if (!f.fix || !state.xlsxPath) return;
    try {
      const r = await fetch("/api/sheetcheck/apply-fix", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          xlsx_path: state.xlsxPath,
          row: f.fix.row,
          column: f.fix.column,
          value: f.fix.value,
        }),
      });
      const data = await r.json();
      if (!r.ok) {
        flashBanner(data.detail || "Apply failed.", "error");
        return;
      }
      flashBanner(`Wrote "${data.new_value}" to row ${data.row}`, "");
      // Re-run the linter so the finding disappears (or shifts) and any
      // dependent findings refresh.
      onRun();
    } catch (e) {
      flashBanner("Network error: " + e.message, "error");
    }
  }

  // ── Make editable copy (read-only → scratch) ─────────────────────
  async function onMakeEditableCopy() {
    if (!state.xlsxPath) return;
    el.editCopyBtn.disabled = true;
    try {
      let r = await fetch("/api/sheetcheck/edit-copy", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ xlsx_path: state.xlsxPath }),
      });
      let data = await r.json();
      if (r.status === 409) {
        // A previous working copy exists. Offer to clobber.
        const ok = await window.modalConfirm(data.detail, {
          title: "A scratch copy already exists",
          confirmLabel: "Replace it",
          kind: "danger",
        });
        if (!ok) return;
        r = await fetch("/api/sheetcheck/edit-copy", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ xlsx_path: state.xlsxPath, overwrite: true }),
        });
        data = await r.json();
      }
      if (!r.ok) {
        flashBanner(data.detail || "Could not make a copy.", "error");
        return;
      }
      // Re-load with the scratch path. Apply buttons now appear.
      el.pathInput.value = data.scratch_path;
      flashBanner("Working copy ready — your fixes won't touch the source.", "");
      onRun();
    } catch (e) {
      flashBanner("Network error: " + e.message, "error");
    } finally {
      el.editCopyBtn.disabled = false;
    }
  }

  // ── Save back to source ──────────────────────────────────────────
  async function onSaveBackToSource() {
    if (!state.xlsxPath || !state.scratchOrigin?.source_path) {
      flashBanner("No source path recorded for this scratch copy.", "error");
      return;
    }
    const target = state.scratchOrigin.source_path;
    const ok = await window.modalConfirm(
      `This will overwrite ${target} with your working copy.\n` +
      `The scratch copy is removed afterwards.`,
      {
        title: "Save back to source?",
        kind: "danger",
        confirmLabel: "Overwrite source",
      }
    );
    if (!ok) return;

    el.saveBackBtn.disabled = true;
    try {
      let r = await fetch("/api/sheetcheck/promote-to-source", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scratch_path: state.xlsxPath }),
      });
      let data = await r.json();
      if (r.status === 409) {
        // Drift: source mtime advanced since we made the copy.
        const force = await window.modalConfirm(
          data.detail + "\n\nForce-overwrite anyway? Excel edits to the source will be lost.",
          {
            title: "Source xlsx changed on disk",
            kind: "danger",
            confirmLabel: "Force overwrite",
          }
        );
        if (!force) return;
        r = await fetch("/api/sheetcheck/promote-to-source", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scratch_path: state.xlsxPath, force: true }),
        });
        data = await r.json();
      }
      if (!r.ok) {
        flashBanner(data.detail || "Save-back failed.", "error");
        return;
      }
      // Re-load against the original source so the page reflects the
      // saved-back state. Apply buttons disappear (path is read-only
      // again), proving the save took.
      el.pathInput.value = data.saved_to;
      flashBanner(`Saved to ${shortPath(data.saved_to)}.`, "");
      onRun();
    } catch (e) {
      flashBanner("Network error: " + e.message, "error");
    } finally {
      el.saveBackBtn.disabled = false;
    }
  }

  function shortPath(p) {
    return (p || "").split(/[\\/]/).slice(-3).join("/");
  }

  // ── Mute / unmute ────────────────────────────────────────────────
  async function toggleMute(target, key, mute) {
    if (!state.xlsxPath) return;
    try {
      const r = await fetch("/api/sheetcheck/suppress", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          xlsx_path: state.xlsxPath,
          target, key,
          action: mute ? "mute" : "unmute",
        }),
      });
      const data = await r.json();
      if (!r.ok) {
        flashBanner(data.detail || "Mute failed.", "error");
        return;
      }
      if (data.error) flashBanner(data.error, "warn");
      // Server is the source of truth. Cheapest way to reconcile is a
      // re-run; the file is small and the round-trip is local.
      onRun();
    } catch (e) {
      flashBanner("Network error: " + e.message, "error");
    }
  }

  // ── Helpers ──────────────────────────────────────────────────────
  function prettyRule(rule) {
    return ({
      blank_required_column: "Blank required",
      suffix_column_mismatch: "Suffix mismatch",
      sku_family_break: "SKU family break",
      image_sku_correlation: "Image misplaced",
      missing_image: "Missing image",
      variant_gap: "Variant gap",
    })[rule] || rule.replace(/_/g, " ");
  }

  function filenameOf(p) {
    if (!p) return "";
    return p.split(/[\\/]/).pop();
  }

  function setBanner(msg, kind) {
    el.banner.textContent = msg;
    el.banner.hidden = false;
    el.banner.dataset.kind = kind || "";
  }
  function hideBanner() { el.banner.hidden = true; }
  function flashBanner(msg, kind) {
    setBanner(msg, kind);
    setTimeout(() => { if (el.banner.textContent === msg) hideBanner(); }, 3500);
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"})[c]);
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/"/g, "&quot;");
  }
  // Wrap quoted SKU-like tokens in suggestion text in <code>: «"A04-20"» → <code>A04-20</code>.
  function highlightCode(s) {
    return escapeHtml(s).replace(/&quot;([^&]+)&quot;/g, '<code>$1</code>');
  }
})();
