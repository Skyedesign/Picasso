/* Themed alert / confirm / prompt that match Picasso's palette.
 *
 * Three async functions on `window`:
 *   await modalAlert(message, opts?)            → undefined
 *   await modalConfirm(message, opts?)          → boolean
 *   await modalPrompt(message, opts?)           → string | null
 *
 * Why custom: the native dialogs ignore the app's CSS, blow up in a
 * separate window decoration, and (for `confirm`) block the install
 * overlay's animation in the M3 updater banner. Tiny vanilla impl
 * keeps the bundle a few KB lighter than dragging in a UI library.
 *
 * `opts` shape (all optional):
 *   - title:        string. Bold heading above the message.
 *   - kind:         'default' | 'danger'. Danger turns the primary
 *                   button red — used for destructive confirms.
 *   - confirmLabel: text on the primary button (default "OK").
 *   - cancelLabel:  text on the secondary button (default "Cancel").
 *   - placeholder:  prompt only; input placeholder.
 *   - default:      prompt only; pre-filled input value.
 *   - pattern:      prompt only; HTML5 input pattern attribute.
 *
 * Closes on:
 *   - Primary button click → resolves (true / value).
 *   - Cancel button, backdrop click, Escape → resolves (false / null /
 *     undefined depending on variant).
 */
(function () {
  "use strict";

  // ── Stylesheet (one-shot, idempotent) ────────────────────────────
  // Page templates already include modals.css. We don't inject styles
  // from JS — keep CSS auditable and overrideable by a page if needed.

  // ── Core builder ─────────────────────────────────────────────────
  // All three variants share the overlay + card chrome; the body
  // (text-only vs text + input) and resolution semantics differ.
  function buildModal(opts) {
    const overlay = document.createElement("div");
    overlay.className = "pm-overlay";

    const card = document.createElement("div");
    card.className = "pm-card";
    if (opts.kind === "danger") card.classList.add("pm-card-danger");
    overlay.appendChild(card);

    if (opts.title) {
      const h = document.createElement("div");
      h.className = "pm-title";
      h.textContent = opts.title;
      card.appendChild(h);
    }

    if (opts.message) {
      const m = document.createElement("div");
      m.className = "pm-message";
      // \n in messages should render as line breaks — easier than
      // making every caller wrap in <p>.
      for (const line of String(opts.message).split("\n")) {
        const p = document.createElement("div");
        p.textContent = line;
        m.appendChild(p);
      }
      card.appendChild(m);
    }

    return { overlay, card };
  }

  function buildButtons(card, { confirmLabel, cancelLabel, kind, showCancel }) {
    const row = document.createElement("div");
    row.className = "pm-actions";

    let cancelBtn = null;
    if (showCancel) {
      cancelBtn = document.createElement("button");
      cancelBtn.type = "button";
      cancelBtn.className = "pm-btn";
      cancelBtn.textContent = cancelLabel || "Cancel";
      row.appendChild(cancelBtn);
    }

    const confirmBtn = document.createElement("button");
    confirmBtn.type = "button";
    confirmBtn.className = "pm-btn pm-btn-primary";
    if (kind === "danger") confirmBtn.classList.add("pm-btn-danger");
    confirmBtn.textContent = confirmLabel || "OK";
    row.appendChild(confirmBtn);

    card.appendChild(row);
    return { confirmBtn, cancelBtn };
  }

  function show(overlay, focusEl) {
    document.body.appendChild(overlay);
    // Force reflow so CSS transitions take. requestAnimationFrame is
    // sufficient — no setTimeout needed.
    requestAnimationFrame(() => overlay.classList.add("pm-visible"));
    if (focusEl) focusEl.focus();
  }

  function close(overlay) {
    overlay.classList.remove("pm-visible");
    // Match the CSS transition (200ms). DOM cleanup after the fade
    // keeps the close animation visible.
    setTimeout(() => overlay.remove(), 200);
  }

  // ── Public: alert ────────────────────────────────────────────────
  function modalAlert(message, opts) {
    opts = opts || {};
    return new Promise((resolve) => {
      const { overlay, card } = buildModal({ ...opts, message });
      const { confirmBtn } = buildButtons(card, {
        confirmLabel: opts.confirmLabel || "OK",
        kind: opts.kind,
        showCancel: false,
      });
      const done = () => { close(overlay); resolve(); };
      confirmBtn.addEventListener("click", done);
      // Escape + backdrop also dismiss — alert is not a question, so
      // any way out works.
      overlay.addEventListener("click", (e) => { if (e.target === overlay) done(); });
      attachEscape(overlay, done);
      show(overlay, confirmBtn);
    });
  }

  // ── Public: confirm ──────────────────────────────────────────────
  function modalConfirm(message, opts) {
    opts = opts || {};
    return new Promise((resolve) => {
      const { overlay, card } = buildModal({ ...opts, message });
      const { confirmBtn, cancelBtn } = buildButtons(card, {
        confirmLabel: opts.confirmLabel || "OK",
        cancelLabel:  opts.cancelLabel  || "Cancel",
        kind: opts.kind,
        showCancel: true,
      });
      const settle = (val) => { close(overlay); resolve(val); };
      confirmBtn.addEventListener("click", () => settle(true));
      cancelBtn.addEventListener("click", () => settle(false));
      overlay.addEventListener("click", (e) => { if (e.target === overlay) settle(false); });
      attachEscape(overlay, () => settle(false));
      // Default focus on Cancel for destructive ops so the user can't
      // dismiss-by-Enter into a delete; safe default elsewhere too.
      show(overlay, opts.kind === "danger" ? cancelBtn : confirmBtn);
    });
  }

  // ── Public: prompt ───────────────────────────────────────────────
  function modalPrompt(message, opts) {
    opts = opts || {};
    return new Promise((resolve) => {
      const { overlay, card } = buildModal({ ...opts, message });

      const inputWrap = document.createElement("div");
      inputWrap.className = "pm-input-wrap";
      const input = document.createElement("input");
      input.type = "text";
      input.className = "pm-input";
      if (opts.placeholder) input.placeholder = opts.placeholder;
      if (opts.default != null) input.value = String(opts.default);
      if (opts.pattern) input.pattern = opts.pattern;
      inputWrap.appendChild(input);
      card.appendChild(inputWrap);

      const { confirmBtn, cancelBtn } = buildButtons(card, {
        confirmLabel: opts.confirmLabel || "OK",
        cancelLabel:  opts.cancelLabel  || "Cancel",
        kind: opts.kind,
        showCancel: true,
      });

      const settle = (val) => { close(overlay); resolve(val); };
      const submit = () => {
        if (opts.pattern && !input.checkValidity()) {
          input.classList.add("pm-input-invalid");
          input.focus();
          return;
        }
        settle(input.value);
      };
      input.addEventListener("input", () => input.classList.remove("pm-input-invalid"));
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); submit(); }
      });
      confirmBtn.addEventListener("click", submit);
      cancelBtn.addEventListener("click", () => settle(null));
      overlay.addEventListener("click", (e) => { if (e.target === overlay) settle(null); });
      attachEscape(overlay, () => settle(null));
      show(overlay, input);
    });
  }

  // ── Escape handler — attached per-modal, removed on close ────────
  function attachEscape(overlay, onEscape) {
    const handler = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onEscape();
      }
    };
    document.addEventListener("keydown", handler);
    // Use a MutationObserver so the keydown listener is removed
    // exactly when the overlay leaves the DOM, regardless of how it
    // got removed.
    const obs = new MutationObserver(() => {
      if (!document.body.contains(overlay)) {
        document.removeEventListener("keydown", handler);
        obs.disconnect();
      }
    });
    obs.observe(document.body, { childList: true, subtree: true });
  }

  // Export.
  window.modalAlert = modalAlert;
  window.modalConfirm = modalConfirm;
  window.modalPrompt = modalPrompt;
})();
