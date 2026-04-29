// imgproc UI — vanilla JS, no build step.
// The backend does all the real work; this file just wires clicks to /api/* calls.

const els = {
  root: document.getElementById('root'),
  batches: document.getElementById('batches'),
  newBatch: document.getElementById('new-batch'),
  importBatch: document.getElementById('import-batch'),
  refresh: document.getElementById('refresh'),
  settings: document.getElementById('settings'),
  saveSettings: document.getElementById('save-settings'),
  reloadSettings: document.getElementById('reload-settings'),
  settingsLaunch: document.getElementById('settings-launch'),
  settingsOverlay: document.getElementById('settings-overlay'),
  settingsClose: document.getElementById('settings-close'),
  logSection: document.getElementById('log-section'),
  log: document.getElementById('log'),
  jobStatus: document.getElementById('job-status'),
  batchRowTpl: document.getElementById('batch-row-tpl'),
  importDialog: document.getElementById('import-dialog'),
  importForm: document.getElementById('import-form'),
  importCancel: document.getElementById('import-cancel'),
  sourcePickerWrap: document.getElementById('source-picker-wrap'),
  sourcePicker: document.getElementById('source-picker'),
  sourceRootLabel: document.getElementById('source-root-label'),
};

// ─── Toast ─────────────────────────────────────────────────────────────
let toastTimer = null;
function toast(msg, isError = false) {
  let t = document.querySelector('.toast');
  if (!t) {
    t = document.createElement('div');
    t.className = 'toast';
    document.body.appendChild(t);
  }
  t.textContent = msg;
  t.classList.toggle('error', isError);
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 2400);
}

// ─── Fetch helpers ─────────────────────────────────────────────────────
async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json();
}

// ─── Batches ───────────────────────────────────────────────────────────
async function loadBatches() {
  try {
    const data = await api('/api/batches');
    els.root.textContent = data.root;
    renderBatches(data.batches);
  } catch (e) {
    toast(`Failed to load batches: ${e.message}`, true);
  }
}

function renderBatches(batches) {
  els.batches.innerHTML = '';
  if (!batches.length) {
    els.batches.innerHTML = '<div class="empty">No batches yet. Click "+ New batch" to create one.</div>';
    return;
  }
  for (const b of batches) {
    const row = els.batchRowTpl.content.firstElementChild.cloneNode(true);
    row.querySelector('.batch-name').textContent = b.name;

    const meta = row.querySelector('.batch-meta');
    const parts = [`${b.image_count} image${b.image_count === 1 ? '' : 's'}`];
    meta.innerHTML = '';
    meta.append(...parts.map(p => {
      const s = document.createElement('span');
      s.textContent = p;
      return s;
    }));
    if (b.has_report) {
      const b1 = document.createElement('span');
      b1.className = 'badge ok';
      b1.textContent = `${b.processed_count} processed`;
      meta.appendChild(b1);
      if (b.review_count) {
        const b2 = document.createElement('span');
        b2.className = 'badge review';
        b2.textContent = `${b.review_count} review`;
        meta.appendChild(b2);
      }
    }

    row.querySelector('.btn-open').addEventListener('click', () => openBatch(b.name));
    const btnReview = row.querySelector('.btn-review');
    // Reviewer needs SOMETHING to show — at minimum, top-level images. Hide
    // when the folder is empty (just-created, no import yet).
    if (b.image_count > 0 || b.has_report) {
      btnReview.hidden = false;
      btnReview.addEventListener('click', () => {
        window.open(`/reviewer/${encodeURIComponent(b.name)}`, '_blank');
      });
    }
    const btnReport = row.querySelector('.btn-report');
    if (b.has_report) {
      btnReport.hidden = false;
      btnReport.addEventListener('click', () => {
        window.open(`/batches/${encodeURIComponent(b.name)}/report.html`, '_blank');
      });
    }
    row.querySelector('.btn-process').addEventListener('click', () => processBatch(b.name));
    row.querySelector('.btn-remove').addEventListener('click', () => removeBatch(b));

    els.batches.appendChild(row);
  }
}

async function newBatch() {
  const name = prompt('Batch name (letters, numbers, dash, underscore, space):');
  if (!name) return;
  try {
    await api('/api/batches', { method: 'POST', body: JSON.stringify({ name: name.trim() }) });
    toast(`Created batch "${name.trim()}"`);
    await loadBatches();
  } catch (e) {
    toast(e.message, true);
  }
}

async function openImportDialog() {
  els.importForm.reset();
  await populateSourcePicker();
  els.importDialog.showModal();
}

async function populateSourcePicker() {
  // Clear previous entries
  els.sourcePicker.innerHTML = '<option value="">— select a folder —</option>';
  try {
    const data = await api('/api/source-folders');
    if (!data.exists || !data.folders.length) {
      els.sourcePickerWrap.hidden = true;
      return;
    }
    els.sourceRootLabel.textContent = data.root;
    for (const f of data.folders) {
      const opt = document.createElement('option');
      opt.value = f.path;
      const rel = f.relative === '.' ? '(root)' : f.relative;
      opt.textContent = `${rel} — ${f.image_count} image${f.image_count === 1 ? '' : 's'}`;
      els.sourcePicker.appendChild(opt);
    }
    els.sourcePickerWrap.hidden = false;
  } catch (err) {
    // Silent degradation — user can still paste a path manually.
    els.sourcePickerWrap.hidden = true;
  }
}

async function handleImportSubmit(e) {
  e.preventDefault();
  const data = new FormData(els.importForm);
  const payload = {
    source_path: (data.get('source_path') || '').toString().trim(),
    name: (data.get('name') || '').toString().trim(),
    move: data.get('move') === 'on',
  };
  const submitBtn = els.importForm.querySelector('button[type="submit"]');
  submitBtn.disabled = true;
  submitBtn.textContent = 'Importing…';
  try {
    const result = await api('/api/batches/import', {
      method: 'POST',
      body: JSON.stringify(payload),
    });
    els.importDialog.close();

    const summary = result.non_white_count
      ? `Imported ${result.imported} — ${result.non_white_count} have non-white backgrounds`
      : `Imported ${result.imported} image${result.imported === 1 ? '' : 's'} into "${result.name}"`;
    toast(summary);

    if (result.non_white_count > 0) {
      const names = result.non_white_files.join('\n');
      const were = result.non_white_count === 1 ? 'image has a non-white background' : 'images have non-white backgrounds';
      alert(
        `${result.non_white_count} of the ${result.imported} imported ${were}:\n\n${names}\n\n` +
        `These will be routed to "skipped/" at process time. Use "Open folder" to review or remove them first if you'd like.`
      );
    }

    await loadBatches();
  } catch (err) {
    toast(err.message, true);
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = 'Import';
  }
}

async function openBatch(name) {
  try {
    await api(`/api/batches/${encodeURIComponent(name)}/open`, { method: 'POST' });
  } catch (e) {
    toast(e.message, true);
  }
}

async function removeBatch(b) {
  // Intentionally explicit — deleting a batch wipes originals, processed/, review/,
  // and the QA report. No undo; user data lives outside this tool.
  const totalImgs = b.image_count + (b.processed_count || 0) + (b.review_count || 0);
  const detail = totalImgs
    ? `\n\nThis will permanently delete ${totalImgs} file${totalImgs === 1 ? '' : 's'} in the folder.`
    : '';
  const ok = confirm(`Remove batch "${b.name}"?${detail}\n\nThis cannot be undone.`);
  if (!ok) return;
  try {
    await api(`/api/batches/${encodeURIComponent(b.name)}`, { method: 'DELETE' });
    toast(`Removed "${b.name}"`);
    await loadBatches();
  } catch (e) {
    toast(`Remove failed: ${e.message}`, true);
  }
}

// ─── Processing ────────────────────────────────────────────────────────
async function processBatch(name) {
  els.logSection.hidden = false;
  els.log.textContent = 'Starting…';
  setJobStatus('running', `${name}: running`);
  try {
    const { job_id } = await api('/api/process', {
      method: 'POST',
      body: JSON.stringify({ name }),
    });
    await pollJob(job_id, name);
  } catch (e) {
    setJobStatus('error', `${name}: ${e.message}`);
    toast(e.message, true);
  }
}

const _PHASE_LABEL = {
  starting:    'Starting',
  scanning:    'Detecting products',
  writing:     'Writing outputs',
  report:      'Writing report',
  done:        'Finishing',
};

async function pollJob(id, batchName) {
  while (true) {
    await new Promise(r => setTimeout(r, 600));
    let job;
    try {
      job = await api(`/api/jobs/${id}`);
    } catch (e) {
      setJobStatus('error', `${batchName}: ${e.message}`);
      return;
    }
    els.log.textContent = job.log || '(running…)';

    if (job.status === 'running') {
      const phase = job.progress && job.progress.phase;
      const label = _PHASE_LABEL[phase] || phase || 'Running';
      setJobStatus('running', `${batchName}: ${label}`, job.progress);
      continue;
    }

    setJobStatus(job.status, `${batchName}: ${job.status}`);
    if (job.status === 'done') {
      await loadBatches();  // pick up new report.html + counts
      if (job.report_url) {
        const link = document.createElement('div');
        link.style.marginTop = '8px';
        link.innerHTML = `<a href="${job.report_url}" target="_blank">Open QA report →</a>`;
        els.log.appendChild(link);
      }
    }
    return;
  }
}

function setJobStatus(status, text, progress) {
  els.jobStatus.className = `job-status ${status}`;
  els.jobStatus.innerHTML = '';

  if (status === 'running') {
    const spinner = document.createElement('span');
    spinner.className = 'spinner';
    els.jobStatus.appendChild(spinner);
  }

  const label = document.createElement('span');
  label.textContent = text;
  els.jobStatus.appendChild(label);

  if (progress && progress.total > 0) {
    const pct = Math.round((progress.current / progress.total) * 100);
    const counter = document.createElement('span');
    counter.className = 'counter';
    counter.textContent = `${progress.current}/${progress.total} (${pct}%)`;
    els.jobStatus.appendChild(counter);

    const bar = document.createElement('span');
    bar.className = 'progress-bar';
    const fill = document.createElement('span');
    fill.className = 'fill';
    fill.style.width = pct + '%';
    bar.appendChild(fill);
    els.jobStatus.appendChild(bar);
  }
}

// ─── Settings ──────────────────────────────────────────────────────────
// Field definitions use plain-English labels & hints. Internal keys match the
// pydantic Config schema so the save payload is unchanged.
const EVERYDAY_FIELDS = [
  {
    key: 'output_canvas',
    label: 'Output image shape',
    hint: 'The size and aspect ratio of the final image. Delicious Display uses 3:4 (600×800); Famous Mountain uses 1:1 (600×600). Per-batch overrides can be set from the Demo Resizer.',
    type: 'canvas_preset',
    presets: [
      { value: [600, 800], label: 'Delicious Display — 600×800 (3:4)' },
      { value: [600, 600], label: 'Famous Mountain — 600×600 (1:1)' },
    ],
  },
  {
    key: 'target_ratio',
    label: 'How big should products appear in the frame?',
    hint: 'Products are resized so they all fill a similar amount of the frame. "Match the group" uses whichever size is most common across the images you\'re processing.',
    type: 'ratio_choice',
  },
  {
    key: 'max_upscale',
    label: 'Can small products be enlarged to match the group?',
    hint: 'If a product is smaller than the target size, it can be made a bit bigger so it doesn\'t look out of place. Large enlargements can make edges slightly soft.',
    type: 'preset',
    presets: [
      { value: 1.0, label: 'No — keep original size' },
      { value: 1.2, label: 'A little — up to 1.2× (recommended)' },
      { value: 1.5, label: 'Moderate — up to 1.5×' },
      { value: 2.0, label: 'A lot — up to 2× (softer edges)' },
    ],
  },
  {
    key: 'padding_pct',
    label: 'White space around each product',
    hint: 'How much empty white space to leave between the product and the edge of the output image.',
    type: 'number_pct',
    step: 0.5, min: 0, max: 25,
  },
  {
    key: 'min_confidence',
    label: 'When to send tricky images to the Review folder',
    hint: 'If the tool can\'t clearly see the product — because the background isn\'t clean or the product touches the edge — it puts the image aside in a "Review" folder so you can check it manually.',
    type: 'preset',
    presets: [
      { value: 0.6, label: 'Lenient — try to process almost everything' },
      { value: 0.8, label: 'Balanced — send clearly uncertain images to Review (recommended)' },
      { value: 0.9, label: 'Strict — send any slightly uncertain image to Review' },
    ],
  },
  {
    key: 'skip_lifestyle',
    label: 'Skip lifestyle / non-white-background images',
    hint: 'Images that aren\'t on a clean white background (e.g. product-in-scene photos) get copied into a separate "Skipped" folder instead of being processed, so they don\'t affect the size calculations for the rest of the group.',
    type: 'choice',
    options: [
      { value: true,  label: 'Yes — skip them (recommended)' },
      { value: false, label: 'No — try to process everything' },
    ],
  },
  {
    key: 'lifestyle_bg_threshold',
    label: 'How white must the background be to count as "clean"?',
    hint: 'Images where fewer than this fraction of the corner pixels are near-white get tagged as lifestyle and skipped. Only applies when the above is turned on.',
    type: 'preset',
    presets: [
      { value: 0.70, label: 'Loose — only skip obvious scene shots' },
      { value: 0.85, label: 'Balanced (recommended)' },
      { value: 0.95, label: 'Strict — require very clean corners' },
    ],
  },
];

const ADVANCED_FIELDS = [
  {
    key: 'tolerance_mad',
    label: 'How strict about calling an image an "outlier"',
    hint: 'How much an image has to differ from the group\'s typical size before it gets resized. Lower = resize more images.',
    type: 'number',
    step: 0.1, min: 0.5, max: 3.0,
  },
  {
    key: 'bg_threshold',
    label: 'How close to white must the background be?',
    hint: 'Pixels brighter than this on all three colour channels (R, G, B) count as background. 245 handles normal product photography. Rarely needs changing.',
    type: 'number',
    step: 1, min: 200, max: 255,
  },
  {
    key: 'recenter',
    label: 'How to centre products in the frame',
    hint: '"By visual weight" places the visually heavy part (the flower head of a stem, the body of a vase) at the centre. "By shape bounds" centres the whole product silhouette.',
    type: 'choice',
    options: [
      { value: true,  label: 'By visual weight (recommended)' },
      { value: false, label: 'By shape bounds' },
    ],
  },
];

let currentConfig = null;

async function loadSettings() {
  try {
    currentConfig = await api('/api/config');
    renderSettings(currentConfig);
  } catch (e) {
    toast(`Failed to load config: ${e.message}`, true);
  }
}

function renderSettings(cfg) {
  els.settings.innerHTML = '';
  // Both accordions start collapsed: makes both sections discoverable on
  // open, and means the most-changed field (Output image size, in Advanced)
  // is one click away regardless of which section it lives in.
  const everyday = makeSection('Everyday settings', { collapsible: true, collapsed: true });
  for (const f of EVERYDAY_FIELDS) everyday.body.appendChild(renderField(f, cfg));
  els.settings.appendChild(everyday.section);

  const advanced = makeSection('Advanced', { collapsible: true, collapsed: true });
  for (const f of ADVANCED_FIELDS) advanced.body.appendChild(renderField(f, cfg));
  els.settings.appendChild(advanced.section);
}

function makeSection(title, { collapsible = false, collapsed = false } = {}) {
  const section = document.createElement('div');
  section.className = 'settings-section';
  const header = document.createElement('div');
  header.className = 'settings-section-header';
  header.textContent = title;
  const body = document.createElement('div');
  body.className = 'settings-section-body';
  if (collapsible) {
    section.classList.add('collapsible');
    if (collapsed) section.classList.add('collapsed');
    const chev = document.createElement('span');
    chev.className = 'chev';
    chev.textContent = '▸';
    header.prepend(chev);
    header.addEventListener('click', () => {
      section.classList.toggle('collapsed');
      chev.textContent = section.classList.contains('collapsed') ? '▸' : '▾';
    });
    if (!collapsed) chev.textContent = '▾';
  }
  section.append(header, body);
  return { section, body };
}

function renderField(f, cfg) {
  const wrap = document.createElement('div');
  wrap.className = 'field';
  wrap.dataset.key = f.key;

  const label = document.createElement('label');
  label.className = 'field-label';
  label.textContent = f.label;
  wrap.appendChild(label);

  const control = document.createElement('div');
  control.className = 'field-control';
  wrap.appendChild(control);

  if (f.type === 'ratio_choice') {
    const auto = cfg.target_ratio === 'auto';
    const radios = makeRadios(f.key, [
      { value: 'auto',   label: 'Match the group\'s typical size (recommended)' },
      { value: 'number', label: 'Set a specific size' },
    ], auto ? 'auto' : 'number');
    control.appendChild(radios.el);

    const numWrap = document.createElement('div');
    numWrap.className = 'sub-control';
    const num = document.createElement('input');
    num.type = 'number';
    num.step = '5';
    num.min = '5';
    num.max = '95';
    num.value = typeof cfg.target_ratio === 'number' ? Math.round(cfg.target_ratio * 100) : 50;
    const unit = document.createElement('span');
    unit.className = 'unit';
    unit.textContent = '% of the frame';
    numWrap.append(num, unit);
    control.appendChild(numWrap);

    const updateDisabled = () => {
      num.disabled = radios.value() === 'auto';
      numWrap.classList.toggle('disabled', num.disabled);
    };
    radios.onChange(updateDisabled);
    updateDisabled();

    wrap._getters = () => radios.value() === 'auto' ? 'auto' : parseFloat(num.value) / 100;
  }
  else if (f.type === 'preset') {
    const current = cfg[f.key];
    const group = document.createElement('div');
    group.className = 'preset-group';
    let selected = current;
    for (const p of f.presets) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'preset';
      btn.textContent = p.label;
      if (Math.abs((current ?? 0) - p.value) < 1e-6) btn.classList.add('active');
      btn.addEventListener('click', () => {
        selected = p.value;
        group.querySelectorAll('.preset').forEach(b => b.classList.toggle('active', b === btn));
      });
      group.appendChild(btn);
    }
    control.appendChild(group);
    wrap._getters = () => selected;
  }
  else if (f.type === 'canvas_preset') {
    // Like 'preset' but values are [W, H] tuples. If the on-disk value
    // doesn't match any preset, no chip activates and the original value
    // is preserved on save (so a hand-edited imgproc.yaml with a custom
    // canvas isn't clobbered just by opening the Settings modal).
    const current = Array.isArray(cfg[f.key]) ? cfg[f.key] : [600, 800];
    let selected = current.slice();
    const group = document.createElement('div');
    group.className = 'preset-group';
    for (const p of f.presets) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'preset';
      btn.textContent = p.label;
      const isActive = current[0] === p.value[0] && current[1] === p.value[1];
      if (isActive) btn.classList.add('active');
      btn.addEventListener('click', () => {
        selected = p.value.slice();
        group.querySelectorAll('.preset').forEach(b => b.classList.toggle('active', b === btn));
      });
      group.appendChild(btn);
    }
    control.appendChild(group);
    wrap._getters = () => selected;
  }
  else if (f.type === 'choice') {
    const current = cfg[f.key];
    const radios = makeRadios(f.key, f.options, current);
    control.appendChild(radios.el);
    wrap._getters = () => radios.value();
  }
  else if (f.type === 'number_pct') {
    const line = document.createElement('div');
    line.className = 'inline';
    const input = document.createElement('input');
    input.type = 'number';
    input.step = f.step;
    if (f.min !== undefined) input.min = f.min;
    if (f.max !== undefined) input.max = f.max;
    input.value = cfg[f.key];
    const unit = document.createElement('span');
    unit.className = 'unit';
    unit.textContent = '%';
    line.append(input, unit);
    control.appendChild(line);
    wrap._getters = () => parseFloat(input.value);
  }
  else { // number
    const input = document.createElement('input');
    input.type = 'number';
    input.step = f.step;
    if (f.min !== undefined) input.min = f.min;
    if (f.max !== undefined) input.max = f.max;
    input.value = cfg[f.key];
    control.appendChild(input);
    wrap._getters = () => parseFloat(input.value);
  }

  const hint = document.createElement('div');
  hint.className = 'hint';
  hint.textContent = f.hint;
  wrap.appendChild(hint);

  return wrap;
}

function makeRadios(name, options, initialValue) {
  const el = document.createElement('div');
  el.className = 'radio-group';
  const inputs = [];
  const listeners = [];
  for (const opt of options) {
    const line = document.createElement('label');
    line.className = 'radio';
    const input = document.createElement('input');
    input.type = 'radio';
    input.name = name;
    input.dataset.value = JSON.stringify(opt.value);
    input.checked = JSON.stringify(opt.value) === JSON.stringify(initialValue);
    input.addEventListener('change', () => listeners.forEach(fn => fn()));
    inputs.push(input);
    const text = document.createElement('span');
    text.textContent = opt.label;
    line.append(input, text);
    el.appendChild(line);
  }
  return {
    el,
    value: () => {
      const sel = inputs.find(i => i.checked);
      return sel ? JSON.parse(sel.dataset.value) : undefined;
    },
    onChange: (fn) => listeners.push(fn),
  };
}

async function saveSettings() {
  const payload = {};
  for (const wrap of els.settings.querySelectorAll('.field')) {
    payload[wrap.dataset.key] = wrap._getters();
  }
  try {
    await api('/api/config', { method: 'POST', body: JSON.stringify(payload) });
    currentConfig = payload;
    toast('Settings saved');
    // Settings are modal-only now; close on success after a beat so the
    // toast is visible. Idempotent if already closed.
    setTimeout(() => { els.settingsOverlay.hidden = true; }, 600);
  } catch (e) {
    toast(`Save failed: ${e.message}`, true);
  }
}

// ─── Wire up ───────────────────────────────────────────────────────────
els.newBatch.addEventListener('click', newBatch);
els.importBatch.addEventListener('click', openImportDialog);
els.importCancel.addEventListener('click', () => els.importDialog.close());
els.importForm.addEventListener('submit', handleImportSubmit);
els.sourcePicker.addEventListener('change', () => {
  if (els.sourcePicker.value) {
    els.importForm.querySelector('input[name="source_path"]').value = els.sourcePicker.value;
    // Pre-fill batch name from the folder's basename if still empty.
    const nameInput = els.importForm.querySelector('input[name="name"]');
    if (!nameInput.value) {
      const basename = els.sourcePicker.value.replace(/[\\\/]$/, '').split(/[\\\/]/).pop() || '';
      nameInput.value = basename.replace(/[^A-Za-z0-9 _\-]/g, '-');
    }
  }
});
els.refresh.addEventListener('click', loadBatches);
els.saveSettings.addEventListener('click', saveSettings);
els.reloadSettings.addEventListener('click', loadSettings);

// ─── Settings modal ────────────────────────────────────────────────────
function openSettings() {
  els.settingsOverlay.hidden = false;
  // Refresh on each open in case a sibling tab or external edit changed imgproc.yaml.
  loadSettings();
}
function closeSettings() { els.settingsOverlay.hidden = true; }

els.settingsLaunch.addEventListener('click', openSettings);
els.settingsClose.addEventListener('click', closeSettings);
els.settingsOverlay.addEventListener('click', (e) => {
  if (e.target === els.settingsOverlay) closeSettings();
});
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && !els.settingsOverlay.hidden) closeSettings();
});

loadBatches();
// Settings load lazily on first open — no upfront fetch.
