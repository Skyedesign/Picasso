// imgproc UI — vanilla JS, no build step.
// The backend does all the real work; this file just wires clicks to /api/* calls.

const els = {
  root: document.getElementById('root'),
  batches: document.getElementById('batches'),
  newBatch: document.getElementById('new-batch'),
  refresh: document.getElementById('refresh'),
  settings: document.getElementById('settings'),
  saveSettings: document.getElementById('save-settings'),
  reloadSettings: document.getElementById('reload-settings'),
  logSection: document.getElementById('log-section'),
  log: document.getElementById('log'),
  jobStatus: document.getElementById('job-status'),
  batchRowTpl: document.getElementById('batch-row-tpl'),
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
    const btnReport = row.querySelector('.btn-report');
    if (b.has_report) {
      btnReport.hidden = false;
      btnReport.addEventListener('click', () => {
        window.open(`/batches/${encodeURIComponent(b.name)}/report.html`, '_blank');
      });
    }
    row.querySelector('.btn-process').addEventListener('click', () => processBatch(b.name));

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

async function openBatch(name) {
  try {
    await api(`/api/batches/${encodeURIComponent(name)}/open`, { method: 'POST' });
  } catch (e) {
    toast(e.message, true);
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

async function pollJob(id, batchName) {
  while (true) {
    await new Promise(r => setTimeout(r, 600));
    let job;
    try {
      job = await api(`/api/jobs/${id}`);
    } catch (e) {
      setJobStatus('error', e.message);
      return;
    }
    els.log.textContent = job.log || '(no output yet)';
    if (job.status === 'running') continue;
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

function setJobStatus(status, text) {
  els.jobStatus.className = `job-status ${status}`;
  els.jobStatus.textContent = text;
}

// ─── Settings ──────────────────────────────────────────────────────────
const FIELDS = [
  { key: 'tolerance_mad',  type: 'number', step: 0.1,   hint: 'Outlier threshold (MAD multiplier).' },
  { key: 'target_ratio',   type: 'ratio',                hint: '"auto" = use group median, or a number 0-1.' },
  { key: 'bg_threshold',   type: 'number', step: 1,     hint: 'Background detection RGB threshold (0-255).' },
  { key: 'padding_pct',    type: 'number', step: 0.5,   hint: 'Min breathing room between product and canvas edge (%).' },
  { key: 'min_confidence', type: 'number', step: 0.05,  hint: 'Below this, images go to review/ instead of processed/.' },
  { key: 'max_upscale',    type: 'number', step: 0.05,  hint: 'Cap on upscaling factor. 1.0 = never upscale.' },
  { key: 'recenter',       type: 'bool',                 hint: 'Center on mask centroid (true) or bbox geometric center (false).' },
  { key: 'output_canvas',  type: 'canvas',               hint: 'Output canvas size (width × height).' },
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
  for (const f of FIELDS) {
    const wrap = document.createElement('div');
    wrap.className = 'field';
    const label = document.createElement('label');
    label.textContent = f.key;
    wrap.appendChild(label);

    if (f.type === 'ratio') {
      // "auto" or number
      const line = document.createElement('div');
      line.className = 'inline';
      const autoLabel = document.createElement('label');
      autoLabel.style.fontWeight = 'normal';
      const autoCb = document.createElement('input');
      autoCb.type = 'checkbox';
      autoCb.checked = cfg.target_ratio === 'auto';
      autoLabel.appendChild(autoCb);
      autoLabel.appendChild(document.createTextNode(' auto'));
      const num = document.createElement('input');
      num.type = 'number';
      num.step = '0.01';
      num.min = '0.01';
      num.max = '0.99';
      num.value = typeof cfg.target_ratio === 'number' ? cfg.target_ratio : 0.5;
      num.disabled = autoCb.checked;
      autoCb.addEventListener('change', () => num.disabled = autoCb.checked);
      line.append(autoLabel, num);
      wrap.appendChild(line);
      wrap.dataset.key = f.key;
      wrap.dataset.type = 'ratio';
      wrap._getters = () => autoCb.checked ? 'auto' : parseFloat(num.value);
    } else if (f.type === 'bool') {
      const input = document.createElement('input');
      input.type = 'checkbox';
      input.checked = !!cfg[f.key];
      wrap.appendChild(input);
      wrap._getters = () => input.checked;
    } else if (f.type === 'canvas') {
      const line = document.createElement('div');
      line.className = 'inline';
      const w = document.createElement('input');
      w.type = 'number';
      w.min = '1';
      w.value = cfg.output_canvas[0];
      const x = document.createElement('span');
      x.textContent = '×';
      const h = document.createElement('input');
      h.type = 'number';
      h.min = '1';
      h.value = cfg.output_canvas[1];
      line.append(w, x, h);
      wrap.appendChild(line);
      wrap._getters = () => [parseInt(w.value, 10), parseInt(h.value, 10)];
    } else {
      const input = document.createElement('input');
      input.type = 'number';
      input.step = f.step;
      input.value = cfg[f.key];
      wrap.appendChild(input);
      wrap._getters = () => parseFloat(input.value);
    }

    const hint = document.createElement('div');
    hint.className = 'hint';
    hint.textContent = f.hint;
    wrap.appendChild(hint);

    wrap.dataset.key = f.key;
    els.settings.appendChild(wrap);
  }
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
  } catch (e) {
    toast(`Save failed: ${e.message}`, true);
  }
}

// ─── Wire up ───────────────────────────────────────────────────────────
els.newBatch.addEventListener('click', newBatch);
els.refresh.addEventListener('click', loadBatches);
els.saveSettings.addEventListener('click', saveSettings);
els.reloadSettings.addEventListener('click', loadSettings);

loadBatches();
loadSettings();
