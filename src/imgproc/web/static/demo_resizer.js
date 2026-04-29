// Picasso Demo Resizer — always-summonable tuning tool.
//
// Source: drop / pick a file, or pick a thumbnail from any imported batch.
// Slider tunes the target fill ratio with ~300ms debounce; a preview repaints
// from the in-process /api/picasso/preview endpoint. Apply targets are
// non-destructive: copy ratio to clipboard, save to localStorage as a named
// preset, or write to a batch's folder.yaml so the next process run picks
// up the new ratio + canvas size.

(function () {
  'use strict';

  const CANVAS_PRESETS = [
    { label: 'Delicious 600×800',    value: [600, 800] },
    { label: 'Famous Mountain 600×600', value: [600, 600] },
  ];

  // ─── State ─────────────────────────────────────────────────────────────
  let _srcB64 = null;
  let _srcExt = 'jpg';
  let _srcLabel = '';
  let _debounce = null;
  let _canvasSize = CANVAS_PRESETS[0].value.slice();
  let _lastReqId = 0;  // monotonic — drop stale responses if user slides fast
  let _onceLoaded = false;

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
  function toast(msg, isError = false) {
    // Reuse the host page's toast if it exists; otherwise draw our own.
    if (window.toast) return window.toast(msg, isError);
    let t = document.querySelector('.toast');
    if (!t) {
      t = document.createElement('div');
      t.className = 'toast';
      document.body.appendChild(t);
    }
    t.textContent = msg;
    t.classList.toggle('error', isError);
    t.classList.add('show');
    clearTimeout(t._timer);
    t._timer = setTimeout(() => t.classList.remove('show'), 2400);
  }

  // ─── Open / close ──────────────────────────────────────────────────────
  function openDemo() {
    $('demo-overlay').hidden = false;
    if (!_onceLoaded) {
      bindOnce();
      _onceLoaded = true;
    }
    showStep('pick');
    $('demo-file-input').value = '';
    _srcB64 = null;
    _srcLabel = '';
    populateCanvasPresets();
    refreshBatchSelect();
    refreshLoadPresetSelect();
  }
  function closeDemo() { $('demo-overlay').hidden = true; }

  function showStep(step) {
    $('demo-step-pick').hidden = step !== 'pick';
    $('demo-step-tune').hidden = step !== 'tune';
  }

  // ─── Step 1: pick image ────────────────────────────────────────────────
  function bindOnce() {
    // Drop zone
    const zone = $('demo-drop-zone');
    const input = $('demo-file-input');
    zone.addEventListener('click', () => input.click());
    zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
    zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
    zone.addEventListener('drop', (e) => {
      e.preventDefault();
      zone.classList.remove('dragover');
      const f = e.dataTransfer.files[0];
      if (f) loadFile(f);
    });
    input.addEventListener('change', (e) => {
      const f = e.target.files[0];
      if (f) loadFile(f);
    });

    // Batch picker
    $('demo-batch-select').addEventListener('change', refreshBatchGrid);
    $('demo-batch-source').addEventListener('change', refreshBatchGrid);

    // Slider
    const slider = $('demo-ratio-slider');
    slider.addEventListener('input', () => {
      $('demo-ratio-value').textContent = parseFloat(slider.value).toFixed(2);
      clearTimeout(_debounce);
      _debounce = setTimeout(renderPreview, 300);
    });

    // Apply targets
    $('demo-copy-ratio').addEventListener('click', copyRatio);
    $('demo-save-preset').addEventListener('click', saveLocalPreset);
    $('demo-load-preset').addEventListener('change', loadLocalPreset);
    $('demo-apply-batch').addEventListener('change', () => {
      $('demo-apply-button').disabled = !$('demo-apply-batch').value;
    });
    $('demo-apply-button').addEventListener('click', applyToBatch);

    // Footer
    $('demo-back').addEventListener('click', () => showStep('pick'));
    $('demo-done').addEventListener('click', closeDemo);
    $('demo-close').addEventListener('click', closeDemo);
    $('demo-overlay').addEventListener('click', (e) => {
      if (e.target === $('demo-overlay')) closeDemo();
    });
  }

  function loadFile(file) {
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = reader.result;
      const comma = dataUrl.indexOf(',');
      _srcB64 = dataUrl.slice(comma + 1);
      _srcExt = (file.name.split('.').pop() || 'jpg').toLowerCase();
      _srcLabel = file.name;
      $('demo-original-img').src = dataUrl;
      $('demo-preview-img').src = dataUrl;  // placeholder until first render
      showStep('tune');
      renderPreview();
    };
    reader.onerror = () => toast('Could not read that file.', true);
    reader.readAsDataURL(file);
  }

  async function refreshBatchSelect() {
    const sel = $('demo-batch-select');
    const applySel = $('demo-apply-batch');
    sel.innerHTML = '<option value="">— select a batch —</option>';
    applySel.innerHTML = '<option value="">— apply to batch —</option>';
    try {
      const res = await fetch('/api/batches');
      if (!res.ok) return;
      const data = await res.json();
      for (const b of data.batches) {
        sel.appendChild(new Option(b.name + ' (' + b.image_count + ')', b.name));
        applySel.appendChild(new Option(b.name, b.name));
      }
    } catch (e) { /* silent — picker just stays empty */ }
  }

  async function refreshBatchGrid() {
    const grid = $('demo-batch-grid');
    grid.innerHTML = '';
    const name = $('demo-batch-select').value;
    const src = $('demo-batch-source').value;
    if (!name) return;
    try {
      const res = await fetch('/api/batches/' + encodeURIComponent(name) + '/images');
      if (!res.ok) return;
      const data = await res.json();
      const list = data[src] || [];
      const sub = src === 'top' ? '' : src;
      for (const fname of list) {
        const url = '/api/batches/' + encodeURIComponent(name) + '/thumbs/' + encodeURIComponent(fname)
                  + (sub ? '?sub=' + sub : '');
        const btn = el('button', { class: 'demo-thumb', title: fname }, [
          el('img', { src: url, alt: fname }),
        ]);
        btn.addEventListener('click', () => pickFromBatch(name, sub, fname));
        grid.appendChild(btn);
      }
      if (!list.length) {
        grid.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:20px;color:var(--muted);font-size:12px;font-style:italic;">No images in '
          + (src === 'top' ? 'originals' : src) + ' for this batch.</div>';
      }
    } catch (e) { /* silent */ }
  }

  async function pickFromBatch(batchName, sub, filename) {
    // Fetch the full image via the static /batches mount, then convert to b64
    // so the rest of the flow is identical to the drop-file path.
    const url = '/batches/' + encodeURIComponent(batchName) + '/' + (sub ? sub + '/' : '') + encodeURIComponent(filename);
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error('HTTP ' + res.status);
      const blob = await res.blob();
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = reader.result;
        const comma = dataUrl.indexOf(',');
        _srcB64 = dataUrl.slice(comma + 1);
        _srcExt = (filename.split('.').pop() || 'jpg').toLowerCase();
        _srcLabel = batchName + (sub ? '/' + sub : '') + '/' + filename;
        $('demo-original-img').src = dataUrl;
        $('demo-preview-img').src = dataUrl;
        showStep('tune');
        renderPreview();
      };
      reader.readAsDataURL(blob);
    } catch (e) {
      toast('Could not load that image: ' + e.message, true);
    }
  }

  // ─── Step 2: tune ──────────────────────────────────────────────────────
  function populateCanvasPresets() {
    const wrap = $('demo-canvas-presets');
    wrap.innerHTML = '';
    for (const p of CANVAS_PRESETS) {
      const btn = el('button', { class: 'preset' + (eqArr(p.value, _canvasSize) ? ' active' : '') }, p.label);
      btn.addEventListener('click', () => {
        _canvasSize = p.value.slice();
        wrap.querySelectorAll('.preset').forEach(b => b.classList.toggle('active', b === btn));
        renderPreview();
      });
      wrap.appendChild(btn);
    }
  }
  function eqArr(a, b) { return a.length === b.length && a.every((x, i) => x === b[i]); }

  async function renderPreview() {
    if (!_srcB64) return;
    const ratio = parseFloat($('demo-ratio-slider').value);
    const status = $('demo-preview-status');
    const spinner = $('demo-preview-spinner');
    spinner.hidden = false;
    status.classList.remove('ok', 'error');
    status.textContent = 'rendering…';
    const reqId = ++_lastReqId;
    try {
      const res = await fetch('/api/picasso/preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_b64: _srcB64,
          ext: _srcExt,
          target_ratio: ratio,
          canvas_size: _canvasSize,
        }),
      });
      if (reqId !== _lastReqId) return;  // stale, user kept sliding
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || ('HTTP ' + res.status));
      }
      const data = await res.json();
      $('demo-preview-img').src = 'data:image/jpeg;base64,' + data.image_b64;
      status.classList.add('ok');
      status.textContent = 'ratio ' + ratio.toFixed(2) + ' · ' + _canvasSize.join('×');
    } catch (e) {
      if (reqId !== _lastReqId) return;
      status.classList.add('error');
      status.textContent = 'error';
      toast('Preview failed: ' + e.message, true);
    } finally {
      if (reqId === _lastReqId) spinner.hidden = true;
    }
  }

  // ─── Apply targets ─────────────────────────────────────────────────────
  function currentRatio() { return parseFloat($('demo-ratio-slider').value); }

  function copyRatio() {
    const r = currentRatio().toFixed(2);
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(r).then(
        () => toast('Copied ' + r),
        () => toast('Copy failed', true)
      );
    } else {
      toast('Clipboard not available; ratio is ' + r);
    }
  }

  // Local preset storage — keyed by a name the user types. Saved presets
  // capture the current ratio + canvas. Lives in localStorage; not synced
  // anywhere because Alida's only on one machine right now.
  const PRESET_KEY = 'picasso.demoPresets';
  function readPresets() {
    try { return JSON.parse(localStorage.getItem(PRESET_KEY) || '{}') || {}; }
    catch { return {}; }
  }
  function writePresets(p) {
    localStorage.setItem(PRESET_KEY, JSON.stringify(p));
  }
  function saveLocalPreset() {
    const name = (prompt('Name this preset (e.g. "Stockings", "Hats"):') || '').trim();
    if (!name) return;
    const presets = readPresets();
    presets[name] = { target_ratio: currentRatio(), canvas_size: _canvasSize.slice() };
    writePresets(presets);
    toast('Saved preset "' + name + '"');
    refreshLoadPresetSelect();
  }
  function refreshLoadPresetSelect() {
    const sel = $('demo-load-preset');
    const presets = readPresets();
    sel.innerHTML = '<option value="">— load preset —</option>';
    for (const name of Object.keys(presets).sort()) {
      sel.appendChild(new Option(name, name));
    }
  }
  function loadLocalPreset() {
    const sel = $('demo-load-preset');
    const name = sel.value;
    if (!name) return;
    const presets = readPresets();
    const p = presets[name];
    if (!p) return;
    $('demo-ratio-slider').value = p.target_ratio;
    $('demo-ratio-value').textContent = p.target_ratio.toFixed(2);
    if (Array.isArray(p.canvas_size)) {
      _canvasSize = p.canvas_size.slice();
      populateCanvasPresets();
    }
    sel.value = '';  // reset for next time
    renderPreview();
  }

  async function applyToBatch() {
    const batch = $('demo-apply-batch').value;
    if (!batch) return;
    const ratio = currentRatio();
    const btn = $('demo-apply-button');
    btn.disabled = true;
    btn.textContent = 'Applying…';
    try {
      const res = await fetch('/api/batches/' + encodeURIComponent(batch) + '/apply-preset', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_ratio: ratio, canvas_size: _canvasSize }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || ('HTTP ' + res.status));
      }
      toast('Applied to "' + batch + '" — re-process the batch to see the change');
    } catch (e) {
      toast('Apply failed: ' + e.message, true);
    } finally {
      btn.disabled = false;
      btn.textContent = 'Apply';
    }
  }

  // ─── Public surface ────────────────────────────────────────────────────
  window.PicassoDemoResizer = {
    open: openDemo,
    close: closeDemo,
  };

  // Wire the launch button if it exists on this page.
  document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('demo-launch');
    if (btn) btn.addEventListener('click', openDemo);
    // Auto-open when the page is /demo (the standalone pop-out).
    if (document.body.dataset.demoAutoOpen === '1') openDemo();
  });
})();
