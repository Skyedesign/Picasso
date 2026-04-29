# Picasso (imgproc) — Claude Code Context

## What This Is
Intelligent product-image processor for Delicious Display. Takes raw
product-photography folders and produces 600×800 white-background
heroes suitable for Alida's pre-buy sheets + downstream Pegasus ingest.

Standalone tool — Alida runs it on any image folder whenever, not only
on folders destined for Pegasus library ingest. Keep that in mind when
deciding where behaviour should live.

Picasso has a local FastAPI web UI on `http://127.0.0.1:8765` that's now
the primary surface: batches list, Demo Resizer (per-image ratio/canvas
tuning), and a per-batch visual reviewer for accept/reject/re-run with
manual bbox + center-point overrides. The CLI still works for headless
runs.

## Companion project: Pegasus
Pegasus (`C:\Codebase\Pegasus`, separate git repo at
`github.com/Skyedesign/pegasus`) is Delicious Display's product
catalog + buy-list + client-portal system. Picasso is the upstream
image-prep step; Pegasus consumes Picasso's output folders for Excel
import.

The two projects are **deliberately loose-coupled** — filesystem is
the only contract. No Python-level imports between them. Dependencies
diverge (Picasso needs Pillow + numpy + imagehash; Pegasus explicitly
avoids those so `pegasus_host.exe` stays ~20 MB).

See [Pegasus CLAUDE.md](../Pegasus/CLAUDE.md) "Picasso handoff"
section for the consumer side of the contract.

## Layout
- `src/imgproc/cli.py` — `imgproc` CLI entry. `process_folder()` is the
  shared pipeline function; the Click command wraps it. The web app
  calls `process_folder` directly with progress + log callbacks.
- `src/imgproc/engine/` — `detect_product`, `compute_group_stats`,
  `normalize_to_canvas`, `detect_background`. Pure functions, in-process
  callable.
- `src/imgproc/ingest/` — `imgproc-sort` (xlsx ↔ image SKU matching CLI),
  `imgproc-hero` (single-image CLI for Pegasus to subprocess), and
  `sortlib.py` (M5 — reusable anchor-load + candidate-hash + ranking +
  dupe-cluster functions; the web Sort UI calls these in-process).
- `src/imgproc/sheetcheck/` — xlsx linter (M4). `rules.py` runs the
  six rules; `suffixes.py` loads `picasso-suffixes.yaml`;
  `suppressions.py` reads/writes the per-xlsx mute sidecar.
- `src/imgproc/output.py` — `resolve_output_path(folder, name, status,
  group=None)` helper. v1.1 sub-batches will pass `group="..."`.
- `src/imgproc/batch_meta.py` — `batch.json` sidecar (Pydantic schema +
  read/write). Distinct from user-edited `folder.yaml`: sidecar is
  machine-written state.
- `src/imgproc/report/writer.py` — HTML QA report generator (still
  produced alongside `batch.json`).
- `src/imgproc/web/app.py` — FastAPI app. Endpoints below.
- `src/imgproc/web/static/` — `index.html` + `reviewer.html` + `demo.html`
  + `sheetcheck.html` + `sort.html` pages, plus their `*.js`/`*.css`.
  Shared `modals.js` exposes `modalAlert/modalConfirm/modalPrompt`
  (themed dialogs replacing native `alert/prompt/confirm`); every
  page loads it. Vanilla — no framework.
- `imgproc.yaml` — global defaults (600×800 canvas, bg threshold 245,
  etc.). `folder.yaml` in any batch overrides for that batch.
- `picasso-suffixes.yaml` — Sheet-check suffix dictionary
  (`-R/-G/-W/...` → COLOUR; `-S/-M/-L/...` → SIZE). Editable; the
  linter loads it at runtime.
- `Picasso.bat` — one-click launcher for the web UI.
- `batches/` — per-run working folders (gitignored).
- `source/` — scratch input folders (gitignored).

## Commands
- `imgproc <folder>` — headless batch resize. Same code path as the web UI's Process button.
- `imgproc-ui` — start the web server (`Picasso.bat` does this).
- `imgproc-sort <source-folder> <xlsx>` — bucket raw images by SKU + rename.
- `imgproc-hero <input> --output <out> --target-ratio <r>` — single-image, used by Pegasus.

## Web endpoints
Batches CRUD, source-folder picker, config get/save, processing job +
poll, all under `/api/`. The interesting ones added with M1/M2:

- `POST /api/picasso/preview` — Demo Resizer: image_b64 in, rendered b64 out.
- `POST /api/batches/{name}/preview/{filename}` — reviewer live preview
  (loads from disk, applies overrides, returns b64).
- `POST /api/batches/{name}/apply-preset` — write target_ratio /
  canvas_size to that batch's `folder.yaml`.
- `GET  /api/batches/{name}/state` — full state from `batch.json`, or a
  scan-fallback view for legacy batches with no sidecar.
- `GET  /api/batches/{name}/images` — image filenames grouped by location.
- `GET  /api/batches/{name}/thumbs/{filename}?w=200&sub=...` — fast JPEG thumbnails.
- `POST /api/batches/{name}/verdict/{filename}` — accept / reject / rerun
  for one image. Reject moves to skipped/; rerun re-renders + writes to
  processed/. Cleans up stale copies in other subfolders.
- `DELETE /api/batches/{name}/verdict/{filename}` — clear verdict; if
  rejected, restore file from skipped/ to its `status`-derived location.
- `POST /api/batches/{name}/verdict-bulk` — same accept/reject across N
  selected images in one round-trip + one sidecar write.
- `GET  /reviewer/{name}` — page (HTML); JS pulls `/state` + thumbs.
- `GET  /demo` — standalone Demo Resizer page (used by the modal's
  "Pop out" link so Alida can keep it open in a separate tab).

Sheet check (M4):
- `GET  /sheetcheck` — page.
- `GET  /api/sheetcheck/source-files` — list `*.xlsx` under `source/`.
- `POST /api/sheetcheck/run` — body `{xlsx_path}`. Returns visible /
  suppressed findings + parse summary.
- `POST /api/sheetcheck/suppress` — body `{xlsx_path, target, key,
  action}`. `target` ∈ {finding, rule}; `action` ∈ {mute, unmute}.

Visual sort (M5):
- `GET  /sort/{batch}` — page.
- `POST /api/sort/run` — body `{xlsx_path, batch_name, threshold,
  loose_threshold, min_margin, dupe_threshold}`. Background job;
  returns `{job_id}`.
- `GET  /api/sort/jobs/{id}` — status + progress (poll while running).
- `GET  /api/sort/result/{id}` — full result (SKUs + ranked matches +
  dupe clusters + candidate list).
- `GET  /api/sort/anchor/{id}/{sku}` — JPEG anchor thumbnail.
- `POST /api/sort/apply` — body `{job_id, mappings: [{sku, hero,
  extras}], overwrite}`. Copies chosen files to
  `{batch}/processed/sorted/{SKU}{-b/-c/...}.{ext}`.

Send to Pegasus (M6):
- `POST /api/batches/{name}/send` — copy `processed/sorted/*` into
  `{sync_folder}/{batch_name}/`. Also copies the batch's attached
  xlsx (if any) alongside, so Pegasus gets the curated bundle in one
  pass. Updates `last_sent_at` etc. on the sidecar. 409s if
  sync_folder isn't configured or sorted/ is empty.
- `GET  /api/batches/{name}/send-status` — re-read `.picasso-ack.json`
  in the destination without sending; surfaces `pegasus_received_at`.

xlsx as batch asset (post-M6):
- `POST /api/batches/{name}/attach-xlsx` — body `{xlsx_path,
  overwrite}`. Copies an xlsx into the batch as the working copy.
- `POST /api/sheetcheck/apply-fix` — body `{xlsx_path, row, column,
  value, expected_old}`. Writes a single cell. Refuses (403) when
  `xlsx_path` is outside `batches/` — source xlsx stays read-only.
- `/api/sheetcheck/run` returns `writable: true` only for batch-local
  xlsx so the UI knows when to surface Apply.
- The Import dialog accepts an optional `xlsx_path`, attaching in
  one step.

## Output Layout (per input folder)
```
<batch_folder>/
  <raw images at top level>             ← originals; never relocated by verdicts
  <name>.xlsx                           ← optional working copy of pre-buy xlsx
  processed/    ← 600×800 white-bg outputs
    sorted/     ← (M5) SKU-renamed copies — the actual ship-list to Pegasus
  review/       ← low-confidence images flagged for human eye
  skipped/      ← lifestyle/non-white auto-filtered + manually-rejected
  report.html + _report_assets/          ← QA report (kept for debug)
  batch.json                              ← sidecar; reviewer's source of truth
  folder.yaml                             ← per-batch user-edited overrides (optional)
```

`processed/` holds the resized white-bg renders; `processed/sorted/` is
the literal ship-list once the visual Sort step has run. M6 (Send to
Pegasus) will be wired to copy from `processed/sorted/`. `skipped/` is
"won't ship" regardless of who decided.

## Reviewer model (M2)
Three orthogonal axes describe each image; the reviewer's filter chips
reflect them:

- **`status`** (immutable) — what Picasso decided at process time.
  Values: `within-tolerance` | `outlier` | `review` | `skipped-<reason>` |
  `unprocessed` | `unknown`.
- **`output_subfolder`** (mutable) — where the file actually lives now.
  Updated by verdict actions: reject → `skipped`, rerun → `processed`,
  un-reject → restore from `status`.
- **`verdict.decision`** — Alida's override: `accepted` | `rejected` |
  `rerun` | `null` (no decision yet).

Filter chips:
- **No verdict** — `!verdict && output_subfolder !== 'skipped'`. The TODO queue.
- **Accepted** — `verdict in {accepted, rerun}` (clicking Run is implicit accept).
- **Rejected** — `verdict == 'rejected'`.
- **Skipped** — `!verdict && output_subfolder == 'skipped'` (Picasso's auto-filter only).
- **Review** — `output_subfolder == 'review'`.
- **Unprocessed** — `!output_subfolder`.

## Padding semantics
`padding_pct` in settings still clamps scale **only on the CLI/group-batch
path**. The per-image override paths (Demo Resizer, reviewer live preview,
reviewer Run) call `_render_with_overrides(..., padding_pct=0)` so
`target_ratio` is the user's explicit request and isn't fought by the
configured padding. `max_upscale` remains the cap on growth in both paths.

## Handoff to Pegasus
Unchanged. Pegasus's `POST /import/excel` consumes a folder of images
alongside an xlsx. Current expected naming (positional, see Pegasus
CLAUDE.md):
- `{product_number}-{image_number}.{ext}` — product-shared
- `{product_number}-{letter}-{image_number}.{ext}` — variant-specific

Picasso's `imgproc-sort` produces SKU-named files (`{SKU}.jpg`,
`{SKU}-b.jpg`, etc.). Reconciliation between positional-numbering
(Pegasus today) and SKU-naming (Picasso) is active work — check the
Pegasus repo's recent commits for the current state before editing
either side.

## Active plan
v1 expansion plan lives at
`C:\Users\ian\.claude\plans\cryptic-meandering-engelbart.md`. Done: M1
(Demo Resizer), M2 (reviewer + foundations), M3 (packaging + auto-
update), M4 (Sheet check / xlsx linter), M5 (visual imgproc-sort +
dupe detection), M6 (Send to Pegasus), the xlsx-as-batch-asset
extension, M7 (Repeat batch with changes), and the modal-polish task
(themed `modalAlert/modalConfirm/modalPrompt` replacing every native
dialog). M8 (column-strip) dropped — Pegasus owns external-recipient
exports. v1.0 is feature-complete; pending only a release tag.

## Packaging + auto-update (M3 — landed)
- `build/picasso.spec` + `build/build.ps1` + `build/picasso_entry.py` —
  PyInstaller --onedir bundle. `collect_all("PIL")` baked into the spec;
  scipy excluded (imagehash limited to phash). Final bundle ~66 MB.
- `install.bat` at project root: drops Desktop / Start Menu / Startup
  shortcuts, writes the install path to `%LOCALAPPDATA%\Picasso\
  install-path.txt` so `update-alida.bat` is path-agnostic.
- `update-alida.bat`: zip-drop fallback. Reads the install-path marker;
  falls back to common locations + prompt if absent.
- `src/imgproc/updater/{github,swap,__init__}.py` — GitHub Releases
  client + spawn-detached-batch swap pattern. Backups land in
  `%LOCALAPPDATA%\Picasso\backups\` (last 3 kept).
- `web/app.py`: `GET /api/updates/check` + `POST /api/updates/install`,
  plus a connect-and-check single-instance probe in `run_server()` that
  opens the browser to the existing instance instead of crashing.
- `web/static/update_banner.js` — amber footer banner on every page;
  install-overlay polls for the server's return + reloads.

To ship a release: from project root, `.\build\build.ps1` produces
`dist\Picasso\` (with install.bat + update-alida.bat already in it).
Zip the folder, tag a `vX.Y.Z` release on github.com/Skyedesign/Picasso,
upload as `picasso-vX.Y.Z.zip`. The in-app banner picks it up.

## Sheet check (M4 — landed)
Read-only xlsx linter for Alida's pre-buy spreadsheets. New top-nav
button "🧾 Sheet check" → `/sheetcheck`.

- Variant detection: rows with a non-blank QTY column.
- Six rules in `src/imgproc/sheetcheck/rules.py`:
  1. `blank_required_column` — SIZE/COLOUR only. DESCRIPTION + MATERIAL
     are exempt (multi-component textile layouts).
  2. `suffix_column_mismatch` — `-G` row with COLOUR=RED etc. Conservative:
     only flags when the cell contains a *competing* keyword from the
     suffix dict, never when value is unknown.
  3. `sku_family_break` — `E0120` between `E01-15` and `E01-25` →
     suggest `E01-20`. Walks every digit-split looking for a sibling
     family with ≥ 2 dashed members.
  4. `image_sku_correlation` — orphan embedded image anchored outside
     every variant's row block.
  5. `missing_image` — variant block with zero anchored images.
  6. `variant_gap` — info-severity. Only short gaps (≤ 3 missing
     siblings) and only families with ≥ 3 members; rows already flagged
     by `sku_family_break` are excluded so a typo doesn't double-count.
- Suffix dictionary: `picasso-suffixes.yaml` (project root). Adding a
  suffix = adding a yaml entry; rules read it at runtime.
- Two-tier suppression: per-finding mute (one row+rule) and per-rule
  mute (file-wide). Persisted as `{xlsx}.picasso-suppressions.json`
  next to the xlsx so mutes travel with the file.
- xlsx is opened `read_only=True, data_only=True`; `BadZipFile` retry
  once for the mid-Excel-save case.
- The xlsx is never written — Alida fixes in Excel and re-runs.

## Visual sort (M5 — landed)
SKU-matching UI that takes a batch + an xlsx and outputs a clean
SKU-named ship-list at `{batch}/processed/sorted/`. Top-nav: each batch
row gets a "Sort" button (visible whenever the batch has top-level
images).

- `src/imgproc/ingest/sortlib.py` exposes the engine: anchor loading
  from a single xlsx (one entry per SKU, bbox-cropped pHash + JPEG
  thumb bytes), bulk candidate hashing with progress, per-SKU
  ranking with strict / margin / weak tiers, and single-link dupe
  clustering. The legacy `imgproc-sort` CLI is untouched and still
  uses its own anchor-load (it's multi-xlsx / category-aware).
- Heavy compute (~50 ms/image with bbox-crop) runs as a background
  thread job stored in `_sort_jobs` (separate from `_jobs`).
  `/api/sort/jobs/{id}` is the poll endpoint.
- `apply` semantics: COPY (never move), prefer `processed/{filename}`
  if present, fall back to the raw top-level original. Source files
  stay intact so the user can re-sort or re-process. Extras land as
  `{SKU}-b.ext`, `{SKU}-c.ext`, …
- Tunables exposed in the UI (defaults match the CLI: strict ≤ 10,
  loose ≤ 18, min margin 4, dupe ≤ 10).
- Hash mode is fixed at `phash` to avoid imagehash's scipy-pulling
  modes (per the v1 plan + the PyInstaller spec excludes scipy).
- UI: tier filter chips, per-SKU rows (anchor thumb + candidate grid),
  click = hero, Shift-click = extra; auto-picks rank-0 for strict-tier
  SKUs, leaves margin/weak blank.

Acceptance verified end-to-end against the `flowers` batch (137 raw
photos) + `2026 CHRISTMAS FLOWERS.xlsx` (66 SKUs): 59 strict /
3 margin / 4 weak; auto-pick + Apply produced 59 SKU-named files in
`processed/sorted/`.

## Send to Pegasus (M6 — landed)
Filesystem-only handoff. Picasso writes a batch's
`processed/sorted/*` into `{sync_folder}/{batch_name}/`; an external
file-sync tool (Syncthing in Alida's setup) replicates that to the
warehouse NUC. Picasso never touches the network.

- `sync_folder` lives in `imgproc.yaml` (default `~/Picasso-to-Pegasus`).
  Blank disables Send entirely (UI hides the button). Settings panel
  has a free-text field for it.
- Send refuses to ship a batch without `processed/sorted/` content —
  un-sorted batches usually aren't what Alida wants on the NUC.
  She must run the visual Sort first.
- `BatchMeta` (sidecar) tracks `last_sent_at`, `last_sent_count`,
  `last_sent_dest`, `pegasus_received_at`. These are surfaced in the
  batches list as small badges so the round-trip status is visible
  without leaving the home page.
- Pegasus side (later): drop `.picasso-ack.json` with
  `{"received_at": "<ISO>"}` into the destination folder after
  ingest. `GET /api/batches/{name}/send-status` is the polling hook;
  it also persists the ack into the sidecar so other clients see it.
- COPY semantics — never moves source files. The source `sorted/` is
  the canonical local copy; the sync folder is replaceable.
- Setup of Syncthing itself is out of code scope per the plan; this
  is just the local-write side.

## xlsx-as-batch-asset (post-M6 — landed)
Picasso is the cleanup workspace; Pegasus is the operating system.
Under that mental model, an xlsx is a first-class batch asset, not an
external pointer. Each batch can carry its own working copy of
Alida's pre-buy xlsx; Sheet check fixes write to the copy; Send
delivers the cleaned xlsx alongside the SKU-named images.

- `BatchMeta.xlsx_filename` is the canonical record. Filename only
  (not absolute path) — file lives at `{batch_folder}/{filename}`.
- Two ways to attach: the Import dialog's optional spreadsheet
  field, or `POST /api/batches/{name}/attach-xlsx`. Both copy
  (never move) so the source xlsx stays pristine.
- Sheet check writes back via `POST /api/sheetcheck/apply-fix`,
  guarded by `_is_inside_batches()` — it 403s on any path outside
  `batches/`. That guard is load-bearing: the source xlsx in
  `source/` is read-only by policy, full stop.
- `Finding.fix` carries a structured `{row, column, value}` payload
  for the auto-fixable rules. Currently:
    * `suffix_column_mismatch` ⇒ replace cell with the suffix's
      first-keyword (e.g. "GREEN" for `-G`).
    * `sku_family_break` ⇒ replace cell with the suggested SKU
      (e.g. `A0420` ⇒ `A04-20`).
  Other rules don't auto-fix (no value to write, or fix is structural).
- Send ships the xlsx alongside `processed/sorted/*`. Pegasus's
  `POST /import/excel` already takes "folder + xlsx" so this just
  satisfies the existing contract from a single sync drop.
- The legacy standalone Sheet check still works on any pasted path
  (read-only). Apply buttons hide when the loaded xlsx isn't
  batch-local — the UI gates by the `writable` flag returned from
  `/api/sheetcheck/run`.

## When editing both projects at the same time
- Open the **Pegasus** Claude Code session (it's the consumer and has
  the full context). Use `cd ../Picasso` from the shell to peek at
  Picasso source; use `git -C ../Picasso ...` to commit there.
- Avoids session-switching friction without merging the repos.

## Gotchas
- `_find_images` (in `cli.py`) only scans the top level; a `processed/`
  subdir from a previous run won't get re-processed (intentional —
  otherwise every run doubles the output).
- Skip filters run BEFORE group stats so lifestyle shots don't drag the
  median toward huge-"product" scenes.
- `folder.yaml` (user-edited config) and `batch.json` (machine state) are
  two separate files. Don't conflate them — different audiences, different
  write patterns.
- Reject is the inverse of un-reject: file moves to `skipped/` on
  reject; reset (DELETE verdict) on a previously-rejected image
  restores the file via the row's `status` field. Status is preserved
  across verdict ops precisely so this restore can work.
- Top-level originals are never relocated by verdict actions —
  `_current_output_subfolder` deliberately ignores them.
- The CLI's `process_folder()` writes both `report.html` and
  `batch.json` at the end of every run. The web reviewer prefers the
  sidecar; the scan fallback is best-effort for legacy batches.
