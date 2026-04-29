// Visual reviewer — grid + expanded view over a single batch's processing
// state. M2.C scope: read-only display of state + reason badges. Verdict
// buttons are visible but disabled-looking; M2.E wires them up to the
// re-run endpoint.

(function () {
  'use strict';

  // Pull batch name from the URL: /reviewer/{name}
  const urlParts = window.location.pathname.split('/').filter(Boolean);
  const BATCH = decodeURIComponent(urlParts[urlParts.length - 1] || '');

  let _state = null;
  let _filter = 'all';

  // Selection mode for bulk accept/reject. _selected is a Set of filenames,
  // intentionally orthogonal to _filter — switching filters preserves the
  // user's current selection.
  let _selectMode = false;
  const _selected = new Set();

  // Verdict-flow state. Reset every time the expanded view opens.
  let _currentImg = null;
  let _modeBbox = false;
  let _modeCentroid = false;
  let _markerBbox = null;       // [l, t, r, b] in NATURAL image pixels
  let _markerCentroid = null;   // [x, y] in NATURAL image pixels
  let _dragStart = null;
  let _overrideCanvas = [600, 800];
  // Live-preview support for the rerun panel.
  let _previewDebounce = null;
  let _previewReqId = 0;        // monotonic — drop responses to superseded requests
  let _savedOutputHTML = null;  // restore on cancel

  const CANVAS_CHIPS = [
    { value: [600, 800], label: 'Delicious 600×800' },
    { value: [600, 600], label: 'Famous Mountain 600×600' },
  ];

  // ─── DOM helpers ───────────────────────────────────────────────────────
  function $(id) { return document.getElementById(id); }
  function el(tag, attrs = {}, children = []) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (k === 'class') e.className = v;
      else if (k === 'dataset') Object.assign(e.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') e.addEventListener(k.slice(2), v);
      else if (v !== undefined && v !== null && v !== false) e.setAttribute(k, v);
    }
    for (const c of [].concat(children)) {
      if (c == null) continue;
      e.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    }
    return e;
  }

  function bandClass(status) {
    if (status === 'within-tolerance') return 'ok';
    if (status === 'outlier') return 'outlier';
    if (status === 'review') return 'review';
    if (status.startsWith('skipped')) return 'skipped';
    if (status === 'unprocessed') return 'unprocessed';
    return 'unknown';
  }

  // ─── Load state ────────────────────────────────────────────────────────
  async function load() {
    if (!BATCH) {
      $('rv-grid').innerHTML = '<div class="rv-empty">No batch in URL.</div>';
      return;
    }
    try {
      const r = await fetch('/api/batches/' + encodeURIComponent(BATCH) + '/state');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      _state = await r.json();
    } catch (e) {
      $('rv-grid').innerHTML = '<div class="rv-empty">Could not load batch: ' + e.message + '</div>';
      return;
    }
    renderHeader();
    renderToolbar();
    renderGrid();
  }

  function renderHeader() {
    $('rv-batch-name').textContent = _state.batch_name;
    if (_state.last_run_timestamp) {
      $('rv-batch-sub').textContent = 'Last processed ' + _state.last_run_timestamp;
    } else {
      $('rv-batch-sub').textContent = 'Not yet processed';
    }

    const stats = _state.stats;
    const summary = $('rv-summary');
    summary.innerHTML = '';
    if (stats) {
      summary.append(
        chip('Total',     stats.n_total),
        chip('Processed', stats.n_processed),
        chip('Review',    stats.n_review),
        chip('Skipped',   stats.n_skipped),
      );
    } else {
      summary.append(chip('Images', _state.images.length));
    }

    $('rv-banner').hidden = !!_state.has_sidecar;
    if (!_state.has_sidecar) {
      $('rv-banner').textContent =
        'No batch.json sidecar — showing a folder scan instead. Re-process the batch to populate full metrics.';
    }
  }

  function chip(label, n) {
    const span = document.createElement('span');
    span.innerHTML = label + ' <b>' + n + '</b>';
    return span;
  }

  function renderToolbar() {
    const tb = $('rv-toolbar');
    tb.innerHTML = '';
    const counts = countByCategory(_state.images);
    // "No verdict" is Alida's working queue — pole position after All so
    // the obvious next click is "show me what's left to decide on."
    // Accepted / Rejected mirror the verdicts she'll actually issue.
    const filters = [
      { key: 'all',        label: 'All',         n: _state.images.length },
      { key: 'pending',    label: 'No verdict',  n: counts.pending },
      { key: 'accepted',   label: 'Accepted',    n: counts.accepted },
      { key: 'rejected',   label: 'Rejected',    n: counts.rejected },
      { key: 'skipped',    label: 'Skipped',     n: counts.skipped },
      { key: 'review',     label: 'Review',      n: counts.review },
      { key: 'unprocessed',label: 'Unprocessed', n: counts.unprocessed },
    ];
    // Every filter is rendered regardless of count — a stable toolbar is
    // less disorienting than chips appearing/disappearing as Alida works
    // through a batch. An empty chip just shows "0" and is still clickable
    // (same way you can click an empty Inbox).
    for (const f of filters) {
      const classes = ['rv-chip'];
      if (_filter === f.key) classes.push('active');
      // Highlight "No verdict" when it's not selected and there's work to do.
      if (f.key === 'pending') {
        classes.push('rv-chip-pending');
        if (f.n === 0) classes.push('rv-chip-pending-empty');
      }
      const btn = el('button', {
        class: classes.join(' '),
        type: 'button',
      }, [f.label, el('span', { class: 'count' }, String(f.n))]);
      btn.addEventListener('click', () => { _filter = f.key; renderToolbar(); renderGrid(); });
      tb.appendChild(btn);
    }
  }

  // "Pending" (a.k.a. "No verdict") is the user's TODO queue — images that
  // still need a manual decision. Auto-skipped images (currently sitting in
  // skipped/ with no verdict) are excluded: Picasso already filtered them,
  // and the Skipped chip is where Alida fishes any out that look wrong.
  // Manually-rejected images also live in skipped/ but they have a verdict,
  // so `!img.verdict` already excludes them.
  function isPending(img) {
    return !img.verdict && img.output_subfolder !== 'skipped';
  }

  // Filters split into three axes:
  //   - decision-based: No verdict (TODO), Accepted, Rejected
  //   - origin-based:   Skipped (Picasso auto-filtered, no verdict),
  //                     Review (Picasso flagged, in review/)
  //   - location-only:  Unprocessed (no output yet)
  // 'rerun' is bucketed with Accepted because clicking Run is the
  // implicit "I want this one" — the user explicitly chose to keep it.
  function isAccepted(img) {
    return !!img.verdict && (img.verdict.decision === 'accepted' || img.verdict.decision === 'rerun');
  }
  function isRejected(img) {
    return !!img.verdict && img.verdict.decision === 'rejected';
  }

  function countByCategory(images) {
    const c = { accepted: 0, rejected: 0, review: 0, skipped: 0, unprocessed: 0, pending: 0 };
    for (const img of images) {
      if (img.output_subfolder === 'review') c.review += 1;
      if (!img.output_subfolder)             c.unprocessed += 1;
      // Skipped (origin) = auto-filtered, no manual verdict.
      if (!img.verdict && img.output_subfolder === 'skipped') c.skipped += 1;
      if (isAccepted(img))     c.accepted += 1;
      else if (isRejected(img)) c.rejected += 1;
      else if (isPending(img))  c.pending += 1;
    }
    return c;
  }

  function passesFilter(img) {
    switch (_filter) {
      case 'all':         return true;
      case 'pending':     return isPending(img);
      case 'accepted':    return isAccepted(img);
      case 'rejected':    return isRejected(img);
      case 'skipped':     return !img.verdict && img.output_subfolder === 'skipped';
      case 'review':      return img.output_subfolder === 'review';
      case 'unprocessed': return !img.output_subfolder;
    }
    return true;
  }

  function renderGrid() {
    const grid = $('rv-grid');
    grid.innerHTML = '';
    const visible = _state.images.filter(passesFilter);
    if (!visible.length) {
      grid.innerHTML = '<div class="rv-empty">No images match this filter.</div>';
      return;
    }
    for (const img of visible) {
      grid.appendChild(buildTile(img));
    }
  }

  function thumbUrl(img) {
    // Prefer the processed/review/skipped output (so the user sees what
    // Picasso produced) — fall back to the original at top-level if that's
    // all there is.
    const sub = img.output_subfolder || '';
    const fname = img.output_filename || img.name;
    const q = sub ? '?sub=' + sub + '&w=200' : '?w=200';
    return '/api/batches/' + encodeURIComponent(BATCH) + '/thumbs/' + encodeURIComponent(fname) + q;
  }

  function buildTile(img) {
    const isSelected = _selected.has(img.name);
    const tile = el('button', {
      class: 'rv-tile' + (isSelected ? ' selected' : ''),
      type: 'button',
      title: img.name,
      dataset: { name: img.name },
    });
    tile.appendChild(el('div', { class: 'rv-tile-img' }, [
      el('img', { src: thumbUrl(img), alt: img.name, loading: 'lazy' }),
    ]));
    const meta = el('div', { class: 'rv-tile-meta' });
    meta.appendChild(el('div', { class: 'rv-tile-name' }, img.name));
    const tags = el('div', { class: 'rv-tile-tags' });
    tags.appendChild(el('span', { class: 'rv-badge ' + bandClass(img.status) }, statusLabel(img.status)));
    if (img.verdict) {
      tags.appendChild(el('span', { class: 'rv-badge verdict-' + img.verdict.decision }, img.verdict.decision));
    }
    meta.appendChild(tags);
    tile.appendChild(meta);
    tile.addEventListener('click', () => {
      if (_selectMode) {
        toggleTileSelection(img.name, tile);
      } else {
        openExpanded(img);
      }
    });
    return tile;
  }

  function statusLabel(status) {
    if (status === 'within-tolerance') return 'within band';
    if (status === 'outlier') return 'outlier';
    if (status === 'review') return 'review';
    if (status.startsWith('skipped-')) return 'skipped: ' + status.slice('skipped-'.length);
    if (status === 'skipped-unknown') return 'skipped';
    return status;
  }

  // ─── Expanded view ─────────────────────────────────────────────────────
  function openExpanded(img) {
    _currentImg = img;
    $('rv-x-name').textContent = img.name;
    // Original at top-level
    $('rv-x-original').src = '/batches/' + encodeURIComponent(BATCH) + '/' + encodeURIComponent(img.name);

    // Output (processed / review / skipped) — may be missing for unprocessed
    const procFrame = $('rv-x-output-frame');
    procFrame.innerHTML = '';
    procFrame.classList.remove('empty');
    if (img.output_subfolder && img.output_filename) {
      const sub = img.output_subfolder;
      const f = img.output_filename;
      const url = '/batches/' + encodeURIComponent(BATCH) + '/' + sub + '/' + encodeURIComponent(f);
      procFrame.appendChild(el('img', { src: url, alt: 'output', loading: 'lazy' }));
    } else {
      procFrame.classList.add('empty');
      procFrame.textContent = 'Not processed yet.';
    }

    renderReason(img);

    // Reset verdict UI to the default (Accept / Reject / Re-run row visible,
    // override panel hidden, no markers, action buttons enabled).
    foldRerunPanel();
    for (const btn of document.querySelectorAll('.rv-action-btn')) btn.disabled = false;
    updateResetButtonVisibility();

    $('rv-expanded-overlay').hidden = false;
  }

  function closeExpanded() {
    $('rv-expanded-overlay').hidden = true;
    foldRerunPanel();
    _currentImg = null;
  }

  function renderReason(img) {
    const r = $('rv-x-reason');
    r.innerHTML = '';
    function pair(k, v) {
      r.appendChild(el('span', { class: 'rv-reason-key' }, k));
      r.appendChild(el('span', { class: 'rv-reason-val' }, v));
    }
    pair('Status', statusLabel(img.status));
    if (typeof img.occupied_ratio === 'number' && img.occupied_ratio > 0)
      pair('Occupied ratio', img.occupied_ratio.toFixed(3));
    if (typeof img.confidence === 'number')
      pair('Confidence', img.confidence.toFixed(2));
    if (typeof img.bg_purity === 'number')
      pair('BG purity', (img.bg_purity * 100).toFixed(1) + '%');

    const stats = _state.stats;
    if (stats && (img.status === 'within-tolerance' || img.status === 'outlier')) {
      pair('Group band',
        stats.lower_bound.toFixed(3) + '–' + stats.upper_bound.toFixed(3) +
        ' (target ' + stats.target_ratio.toFixed(3) + ')');
    }
  }

  // ─── Verdict + override flow ───────────────────────────────────────────

  function foldRerunPanel(restoreOutput = true) {
    // Restore the default action row; hide override panel + overlay.
    $('rv-override-panel').hidden = true;
    $('rv-expanded-actions').hidden = false;
    $('rv-expanded-rerun-actions').hidden = true;
    $('rv-x-overlay').hidden = true;
    $('rv-x-mode-hint').textContent = '';
    $('rv-x-output-hint').textContent = '';
    _modeBbox = _modeCentroid = false;
    _markerBbox = _markerCentroid = null;
    _dragStart = null;
    clearTimeout(_previewDebounce);
    _previewReqId++;  // stale-out any inflight preview
    for (const c of document.querySelectorAll('.rv-mode-chip')) c.classList.remove('active');
    redrawMarkers();
    // On cancel, put the previously-saved output back. On run-success the
    // caller passes `restoreOutput=false` because the output frame already
    // shows the just-rendered b64.
    if (restoreOutput && _savedOutputHTML !== null) {
      const frame = $('rv-x-output-frame');
      frame.innerHTML = _savedOutputHTML;
      frame.classList.toggle('empty', _savedOutputHTML === '');
    }
    _savedOutputHTML = null;
  }

  function openRerunPanel() {
    $('rv-expanded-actions').hidden = true;
    $('rv-expanded-rerun-actions').hidden = false;
    $('rv-override-panel').hidden = false;
    $('rv-x-overlay').hidden = false;

    // Capture the saved output so Cancel can restore it. Save the markup
    // rather than the URL so we round-trip the empty-state class too.
    _savedOutputHTML = $('rv-x-output-frame').innerHTML;

    // If this image already has a 'rerun' verdict, pre-populate the panel
    // with the previous override values so re-rerun is just a tweak.
    // Otherwise fall back to the batch's group target + last-run canvas.
    const v = _currentImg && _currentImg.verdict;
    const stats = _state && _state.stats;
    const lastCfg = _state && _state.last_run_config;
    let initRatio, initCanvas, initBbox = null, initCentroid = null;
    if (v && v.decision === 'rerun') {
      initRatio = v.target_ratio;
      initCanvas = v.canvas_size;
      initBbox = v.bbox || null;
      initCentroid = v.centroid || null;
    }
    if (initRatio == null) initRatio = (stats && typeof stats.target_ratio === 'number') ? stats.target_ratio : 0.65;
    if (initCanvas == null) initCanvas = (lastCfg && lastCfg.output_canvas) ? lastCfg.output_canvas : [600, 800];

    const slider = $('rv-override-ratio');
    slider.value = initRatio;
    $('rv-override-ratio-val').textContent = Number(initRatio).toFixed(2);

    _overrideCanvas = initCanvas.slice();
    renderCanvasChips();

    _markerBbox = initBbox;
    _markerCentroid = initCentroid;
    _dragStart = null;
    syncOverlayPosition();
    redrawMarkers();
    updateOverrideSummary();
  }

  function renderCanvasChips() {
    const w = $('rv-override-canvas-chips');
    w.innerHTML = '';
    for (const c of CANVAS_CHIPS) {
      const isActive = eqArr(c.value, _overrideCanvas);
      const btn = el('button', { type: 'button', class: 'preset' + (isActive ? ' active' : '') }, c.label);
      btn.addEventListener('click', () => {
        _overrideCanvas = c.value.slice();
        w.querySelectorAll('.preset').forEach(b => b.classList.toggle('active', b === btn));
        schedulePreview(/*immediate=*/true);
      });
      w.appendChild(btn);
    }
  }

  function eqArr(a, b) { return Array.isArray(a) && Array.isArray(b) && a.length === b.length && a.every((x, i) => x === b[i]); }

  function setMode(mode) {
    _modeBbox = mode === 'bbox';
    _modeCentroid = mode === 'centroid';
    for (const c of document.querySelectorAll('.rv-mode-chip')) {
      c.classList.toggle('active', c.dataset.mode === mode);
    }
    const overlay = $('rv-x-overlay');
    overlay.classList.toggle('bbox-mode', _modeBbox);
    overlay.classList.toggle('centroid-mode', _modeCentroid);
    $('rv-x-mode-hint').textContent =
      _modeBbox ? '(drag a rectangle on the original)' :
      _modeCentroid ? '(click on the original to set the center)' : '';
  }

  function clearMarkers() {
    _markerBbox = null;
    _markerCentroid = null;
    redrawMarkers();
    updateOverrideSummary();
    schedulePreview(/*immediate=*/true);
  }

  // ─── Live preview of the rerun ─────────────────────────────────────────
  // Calls /api/batches/{name}/preview/{filename} with the current control
  // values; the response b64 swaps into the output pane. Slider input is
  // debounced (~250ms); discrete actions (chip click, marker change) fire
  // immediately. Stale responses are dropped via a monotonic request id.

  function schedulePreview(immediate = false) {
    clearTimeout(_previewDebounce);
    if (immediate) {
      renderRerunPreview();
    } else {
      _previewDebounce = setTimeout(renderRerunPreview, 250);
    }
  }

  async function renderRerunPreview() {
    if (!_currentImg) return;
    const reqId = ++_previewReqId;
    const body = {
      target_ratio: parseFloat($('rv-override-ratio').value),
      canvas_size: _overrideCanvas,
    };
    if (_markerBbox) body.bbox = _markerBbox.map(Math.round);
    if (_markerCentroid) body.centroid = _markerCentroid.map(x => Math.round(x * 10) / 10);
    const hint = $('rv-x-output-hint');
    hint.textContent = 'rendering…';
    hint.classList.remove('error');
    try {
      const r = await fetch(
        '/api/batches/' + encodeURIComponent(BATCH) +
        '/preview/' + encodeURIComponent(_currentImg.name),
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        }
      );
      if (reqId !== _previewReqId) return;  // superseded
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        let msg = err.detail;
        if (Array.isArray(msg)) msg = msg.map(m => m.msg || JSON.stringify(m)).join('; ');
        throw new Error(msg || ('HTTP ' + r.status));
      }
      const data = await r.json();
      const frame = $('rv-x-output-frame');
      frame.innerHTML = '';
      frame.classList.remove('empty');
      frame.appendChild(el('img', { src: 'data:image/jpeg;base64,' + data.image_b64, alt: 'preview' }));
      hint.textContent = 'preview · ' + parseFloat($('rv-override-ratio').value).toFixed(2) + ' · ' + _overrideCanvas.join('×');
    } catch (e) {
      if (reqId !== _previewReqId) return;
      hint.textContent = 'preview failed: ' + e.message;
      hint.classList.add('error');
    }
  }

  // ─── Coordinate translation ────────────────────────────────────────────
  // The img element uses `object-fit: contain`, which letterboxes the
  // image inside a wider/taller frame. The overlay must match the actual
  // displayed image rectangle, not the IMG element's full box.

  function getDisplayedImageBox() {
    const img = $('rv-x-original');
    const nW = img.naturalWidth, nH = img.naturalHeight;
    const cW = img.clientWidth, cH = img.clientHeight;
    if (!nW || !nH || !cW || !cH) return null;
    const scale = Math.min(cW / nW, cH / nH);
    const dW = nW * scale, dH = nH * scale;
    return {
      offsetX: (cW - dW) / 2,
      offsetY: (cH - dH) / 2,
      displayedW: dW,
      displayedH: dH,
      scale: scale,
    };
  }

  function syncOverlayPosition() {
    const box = getDisplayedImageBox();
    const overlay = $('rv-x-overlay');
    if (!box) { overlay.hidden = true; return; }
    const img = $('rv-x-original');
    overlay.style.left = (img.offsetLeft + box.offsetX) + 'px';
    overlay.style.top = (img.offsetTop + box.offsetY) + 'px';
    overlay.style.width = box.displayedW + 'px';
    overlay.style.height = box.displayedH + 'px';
  }

  function pageToNatural(clientX, clientY) {
    const overlay = $('rv-x-overlay');
    const rect = overlay.getBoundingClientRect();
    const localX = clientX - rect.left;
    const localY = clientY - rect.top;
    if (localX < 0 || localY < 0 || localX > rect.width || localY > rect.height) return null;
    const box = getDisplayedImageBox();
    if (!box) return null;
    return [localX / box.scale, localY / box.scale];
  }

  function naturalToOverlay(nx, ny) {
    const box = getDisplayedImageBox();
    if (!box) return [0, 0];
    return [nx * box.scale, ny * box.scale];
  }

  function redrawMarkers() {
    const overlay = $('rv-x-overlay');
    overlay.innerHTML = '';
    if (_markerBbox) {
      const [l, t, r, b] = _markerBbox;
      const [oL, oT] = naturalToOverlay(l, t);
      const [oR, oB] = naturalToOverlay(r, b);
      const div = document.createElement('div');
      div.className = 'rv-marker-bbox';
      div.style.left = oL + 'px';
      div.style.top = oT + 'px';
      div.style.width = Math.max(2, oR - oL) + 'px';
      div.style.height = Math.max(2, oB - oT) + 'px';
      overlay.appendChild(div);
    }
    if (_markerCentroid) {
      const [cx, cy] = _markerCentroid;
      const [oX, oY] = naturalToOverlay(cx, cy);
      const div = document.createElement('div');
      div.className = 'rv-marker-centroid';
      div.style.left = oX + 'px';
      div.style.top = oY + 'px';
      overlay.appendChild(div);
    }
  }

  function updateOverrideSummary() {
    const parts = [];
    if (_markerCentroid) {
      parts.push('centroid (' + Math.round(_markerCentroid[0]) + ', ' + Math.round(_markerCentroid[1]) + ')');
    }
    if (_markerBbox) {
      const [l, t, r, b] = _markerBbox;
      parts.push('bbox (' + Math.round(l) + ', ' + Math.round(t) + ') → (' + Math.round(r) + ', ' + Math.round(b) + ')');
    }
    $('rv-override-summary').textContent = parts.join(' · ') ||
      'No bbox / centroid override — pick a mode above to set one.';
  }

  // ─── Mouse handlers (overlay) ──────────────────────────────────────────
  function onOverlayMouseDown(e) {
    if (_modeCentroid) {
      const nat = pageToNatural(e.clientX, e.clientY);
      if (nat) {
        _markerCentroid = nat;
        redrawMarkers();
        updateOverrideSummary();
        schedulePreview(/*immediate=*/true);
      }
    } else if (_modeBbox) {
      const nat = pageToNatural(e.clientX, e.clientY);
      if (nat) {
        _dragStart = nat;
        _markerBbox = [nat[0], nat[1], nat[0], nat[1]];
        redrawMarkers();
        e.preventDefault();
      }
    }
  }
  function onOverlayMouseMove(e) {
    if (!_dragStart || !_modeBbox) return;
    const nat = pageToNatural(e.clientX, e.clientY);
    if (!nat) return;
    const l = Math.min(_dragStart[0], nat[0]);
    const t = Math.min(_dragStart[1], nat[1]);
    const r = Math.max(_dragStart[0], nat[0]);
    const b = Math.max(_dragStart[1], nat[1]);
    _markerBbox = [l, t, r, b];
    redrawMarkers();
  }
  function onWindowMouseUp(e) {
    if (_dragStart && _modeBbox) {
      // Drop near-zero rectangles (accidental clicks).
      if (_markerBbox && (_markerBbox[2] - _markerBbox[0] < 4 || _markerBbox[3] - _markerBbox[1] < 4)) {
        _markerBbox = null;
        redrawMarkers();
      }
      updateOverrideSummary();
      schedulePreview(/*immediate=*/true);
    }
    _dragStart = null;
  }

  function updateResetButtonVisibility() {
    const btn = $('rv-act-reset');
    if (!btn) return;
    btn.hidden = !(_currentImg && _currentImg.verdict);
  }

  async function resetVerdict() {
    if (!_currentImg) return;
    const filename = _currentImg.name;
    for (const b of document.querySelectorAll('.rv-action-btn')) b.disabled = true;
    try {
      const r = await fetch(
        '/api/batches/' + encodeURIComponent(BATCH) + '/verdict/' + encodeURIComponent(filename),
        { method: 'DELETE' }
      );
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || ('HTTP ' + r.status));
      }
      // Reload state so the grid badge clears and counts update.
      await load();
      const updated = _state && _state.images.find(i => i.name === filename);
      if (updated) {
        _currentImg = updated;
        renderReason(updated);
        // If the reset moved the file (un-reject restore), refresh the
        // output frame from the new location.
        const procFrame = $('rv-x-output-frame');
        procFrame.innerHTML = '';
        procFrame.classList.remove('empty');
        if (updated.output_subfolder && updated.output_filename) {
          const url = '/batches/' + encodeURIComponent(BATCH) + '/'
            + updated.output_subfolder + '/' + encodeURIComponent(updated.output_filename);
          procFrame.appendChild(el('img', { src: url, alt: 'output', loading: 'lazy' }));
        } else {
          procFrame.classList.add('empty');
          procFrame.textContent = 'Not processed yet.';
        }
      }
      updateResetButtonVisibility();
    } catch (e) {
      alert('Reset failed: ' + e.message);
    } finally {
      for (const b of document.querySelectorAll('.rv-action-btn')) b.disabled = false;
    }
  }

  // ─── Submit verdict ────────────────────────────────────────────────────
  async function submitVerdict(body, expectPreview = false) {
    if (!_currentImg) return;
    const filename = _currentImg.name;
    for (const b of document.querySelectorAll('.rv-action-btn')) b.disabled = true;
    try {
      const r = await fetch('/api/batches/' + encodeURIComponent(BATCH) + '/verdict/' + encodeURIComponent(filename), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        let msg = err.detail;
        if (Array.isArray(msg)) msg = msg.map(m => m.msg || JSON.stringify(m)).join('; ');
        throw new Error(msg || ('HTTP ' + r.status));
      }
      const data = await r.json();
      if (expectPreview && data.image_b64) {
        // Swap the output panel with the freshly-rendered preview — no
        // round-trip to fetch the file.
        const frame = $('rv-x-output-frame');
        frame.innerHTML = '';
        frame.classList.remove('empty');
        frame.appendChild(el('img', { src: 'data:image/jpeg;base64,' + data.image_b64, alt: 'output' }));
      }
      // Reload server-side state so the grid reflects new badges.
      await load();
      // Find the (possibly-updated) row for the current image and refresh
      // the reason panel + verdict badge inline.
      if (_state) {
        const updated = _state.images.find(i => i.name === filename);
        if (updated) {
          _currentImg = updated;
          renderReason(updated);
          updateResetButtonVisibility();
        }
      }
      if (body.decision !== 'rerun') {
        closeExpanded();
      } else {
        // The output frame already shows the freshly-rendered b64; don't
        // overwrite it with the previously-saved output on fold.
        foldRerunPanel(/*restoreOutput=*/false);
      }
    } catch (e) {
      alert('Verdict failed: ' + e.message);
    } finally {
      for (const b of document.querySelectorAll('.rv-action-btn')) b.disabled = false;
    }
  }

  // ─── Bulk select / verdict ─────────────────────────────────────────────

  function toggleSelectMode(force) {
    _selectMode = (typeof force === 'boolean') ? force : !_selectMode;
    document.body.classList.toggle('rv-selecting', _selectMode);
    $('rv-bulk-bar').hidden = !_selectMode;
    const toggle = $('rv-select-toggle');
    toggle.classList.toggle('active', _selectMode);
    toggle.textContent = _selectMode ? '✓ Selecting' : '☐ Select';
    if (!_selectMode) {
      _selected.clear();
      // Re-render so any selected-class rings disappear.
      renderGrid();
    }
    updateBulkBar();
  }

  function toggleTileSelection(name, tile) {
    if (_selected.has(name)) {
      _selected.delete(name);
      tile.classList.remove('selected');
    } else {
      _selected.add(name);
      tile.classList.add('selected');
    }
    updateBulkBar();
  }

  function selectAllVisible() {
    if (!_state) return;
    for (const img of _state.images) {
      if (passesFilter(img)) _selected.add(img.name);
    }
    renderGrid();
    updateBulkBar();
  }

  function clearSelection() {
    _selected.clear();
    // Update the visible tiles in place rather than full re-render so the
    // user doesn't see the grid flash.
    for (const tile of document.querySelectorAll('.rv-tile.selected')) tile.classList.remove('selected');
    updateBulkBar();
  }

  function updateBulkBar() {
    const n = _selected.size;
    $('rv-bulk-count').textContent = n + ' selected';
    const enableActions = _selectMode && n > 0;
    $('rv-bulk-accept').disabled = !enableActions;
    $('rv-bulk-reject').disabled = !enableActions;
    $('rv-bulk-clear').disabled  = !enableActions;
  }

  async function submitBulkVerdict(decision) {
    if (!_selected.size) return;
    const filenames = Array.from(_selected);
    // Disable the bar during request to prevent double-clicks.
    for (const id of ['rv-bulk-accept', 'rv-bulk-reject', 'rv-bulk-clear', 'rv-bulk-select-visible', 'rv-bulk-done']) {
      $(id).disabled = true;
    }
    try {
      const r = await fetch('/api/batches/' + encodeURIComponent(BATCH) + '/verdict-bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision, filenames }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(err.detail || ('HTTP ' + r.status));
      }
      const data = await r.json();
      if (data.errors && data.errors.length) {
        // Surface partial failures without breaking the flow.
        const sample = data.errors.slice(0, 3).map(e => e.filename + ': ' + e.error).join('\n');
        alert('Applied to ' + data.applied + ' images. ' + data.errors.length + ' failed:\n\n' + sample);
      }
      _selected.clear();
      await load();  // refresh state + grid; re-render preserves _selectMode
    } catch (e) {
      alert('Bulk ' + decision + ' failed: ' + e.message);
    } finally {
      for (const id of ['rv-bulk-select-visible', 'rv-bulk-done']) $(id).disabled = false;
      updateBulkBar();
    }
  }

  // ─── Wire ──────────────────────────────────────────────────────────────
  document.addEventListener('DOMContentLoaded', () => {
    $('rv-expanded-close').addEventListener('click', closeExpanded);
    $('rv-expanded-overlay').addEventListener('click', (e) => {
      if (e.target === $('rv-expanded-overlay')) closeExpanded();
    });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape' && !$('rv-expanded-overlay').hidden) closeExpanded();
    });

    // Bulk select bar
    $('rv-select-toggle').addEventListener('click', () => toggleSelectMode());
    $('rv-bulk-done').addEventListener('click', () => toggleSelectMode(false));
    $('rv-bulk-clear').addEventListener('click', clearSelection);
    $('rv-bulk-select-visible').addEventListener('click', selectAllVisible);
    $('rv-bulk-accept').addEventListener('click', () => submitBulkVerdict('accepted'));
    $('rv-bulk-reject').addEventListener('click', () => submitBulkVerdict('rejected'));

    // Action buttons (single-image)
    $('rv-act-accept').addEventListener('click', () => submitVerdict({ decision: 'accepted' }));
    $('rv-act-reject').addEventListener('click', () => submitVerdict({ decision: 'rejected' }));
    $('rv-act-reset').addEventListener('click', resetVerdict);
    $('rv-act-rerun').addEventListener('click', openRerunPanel);
    $('rv-act-rerun-cancel').addEventListener('click', () => foldRerunPanel(/*restoreOutput=*/true));
    $('rv-act-rerun-run').addEventListener('click', () => {
      const body = {
        decision: 'rerun',
        target_ratio: parseFloat($('rv-override-ratio').value),
        canvas_size: _overrideCanvas,
      };
      if (_markerBbox) body.bbox = _markerBbox.map(Math.round);
      if (_markerCentroid) body.centroid = _markerCentroid.map(x => Math.round(x * 10) / 10);
      submitVerdict(body, /*expectPreview=*/true);
    });

    // Override panel controls
    $('rv-override-ratio').addEventListener('input', (e) => {
      $('rv-override-ratio-val').textContent = parseFloat(e.target.value).toFixed(2);
      schedulePreview();  // debounced
    });
    for (const chip of document.querySelectorAll('.rv-mode-chip')) {
      chip.addEventListener('click', () => {
        if (chip.dataset.mode === 'clear') clearMarkers();
        else setMode(chip.dataset.mode);
      });
    }

    // Overlay mouse events (bbox drag + centroid click)
    $('rv-x-overlay').addEventListener('mousedown', onOverlayMouseDown);
    $('rv-x-overlay').addEventListener('mousemove', onOverlayMouseMove);
    window.addEventListener('mouseup', onWindowMouseUp);
    // Recompute overlay rectangle whenever the original image (re)loads.
    $('rv-x-original').addEventListener('load', () => {
      syncOverlayPosition();
      redrawMarkers();
    });
    window.addEventListener('resize', () => {
      syncOverlayPosition();
      redrawMarkers();
    });

    load();
  });
})();
