// Update banner — runs on every Picasso page (index, reviewer, demo).
// On load: pings /api/updates/check; if a newer release is available,
// shows a footer banner. Click → POST /api/updates/install → "Updating…"
// overlay → polls for the server's return → reloads.

(function () {
  'use strict';

  async function check() {
    let data;
    try {
      const r = await fetch('/api/updates/check', { cache: 'no-store' });
      if (!r.ok) return;
      data = await r.json();
    } catch (_) {
      return;  // network down → silent no-op
    }
    setVersion(data.current_version);
    if (data.has_update && data.is_frozen) {
      showBanner(data);
    }
  }

  function setVersion(v) {
    const el = document.getElementById('app-version');
    if (el && v) el.textContent = 'Picasso ' + v;
  }

  function showBanner(info) {
    const banner = document.getElementById('app-update-banner');
    const link = document.getElementById('app-update-link');
    if (!banner || !link) return;
    link.textContent = 'Update to v' + info.latest_version;
    link.title = info.release_notes || ('Release notes: ' + info.release_url);
    link.onclick = (e) => { e.preventDefault(); install(info); };
    banner.hidden = false;
  }

  async function install(info) {
    const ok = await window.modalConfirm(
      'The server will restart automatically. Your batches and settings are preserved.',
      {
        title: 'Update Picasso from v' + info.current_version + ' to v' + info.latest_version + '?',
        confirmLabel: 'Update',
      }
    );
    if (!ok) return;
    showOverlay(info);
    try {
      await fetch('/api/updates/install', { method: 'POST' });
    } catch (_) {
      // The server exits mid-response by design — ignore network failures.
    }
    pollForRestart();
  }

  function showOverlay(info) {
    let ov = document.getElementById('app-updating-overlay');
    if (!ov) {
      ov = document.createElement('div');
      ov.id = 'app-updating-overlay';
      ov.innerHTML =
        '<div class="updating-card">' +
        '  <div class="updating-spinner"></div>' +
        '  <div class="updating-title">Updating Picasso</div>' +
        '  <div class="updating-sub">v' + info.current_version + ' &rarr; v' + info.latest_version + '</div>' +
        '  <div class="updating-status" id="updating-status">Downloading new version…</div>' +
        '</div>';
      document.body.appendChild(ov);
    }
    ov.style.display = 'flex';
  }

  function setStatus(msg) {
    const el = document.getElementById('updating-status');
    if (el) el.textContent = msg;
  }

  async function pollForRestart() {
    // Phase 1: wait for the server to disappear.
    setStatus('Stopping current version…');
    let serverDown = false;
    for (let i = 0; i < 40; i++) {
      await new Promise(r => setTimeout(r, 500));
      try {
        const r = await fetch('/api/updates/check', { cache: 'no-store', signal: timeoutSignal(800) });
        if (!r.ok) { serverDown = true; break; }
      } catch (_) {
        serverDown = true;
        break;
      }
    }
    if (!serverDown) {
      setStatus('Server didn\'t stop — please try again.');
      return;
    }

    // Phase 2: wait for the new version to come back.
    setStatus('Swapping in new version…');
    for (let i = 0; i < 120; i++) {
      await new Promise(r => setTimeout(r, 1000));
      try {
        const r = await fetch('/api/updates/check', { cache: 'no-store', signal: timeoutSignal(800) });
        if (r.ok) {
          setStatus('Done — reloading…');
          await new Promise(r => setTimeout(r, 600));
          location.reload();
          return;
        }
      } catch (_) {
        // still down — keep waiting
      }
    }
    setStatus('Update is taking longer than expected. Refresh the page in a minute.');
  }

  function timeoutSignal(ms) {
    if (typeof AbortSignal !== 'undefined' && AbortSignal.timeout) return AbortSignal.timeout(ms);
    return undefined;
  }

  // Expose for callers that already wire their own DOMContentLoaded.
  window.checkForUpdates = check;

  document.addEventListener('DOMContentLoaded', check);
})();
