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
- `src/imgproc/ingest/` — `imgproc-sort` (xlsx ↔ image SKU matching),
  `imgproc-hero` (single-image CLI for Pegasus to subprocess).
- `src/imgproc/output.py` — `resolve_output_path(folder, name, status,
  group=None)` helper. v1.1 sub-batches will pass `group="..."`.
- `src/imgproc/batch_meta.py` — `batch.json` sidecar (Pydantic schema +
  read/write). Distinct from user-edited `folder.yaml`: sidecar is
  machine-written state.
- `src/imgproc/report/writer.py` — HTML QA report generator (still
  produced alongside `batch.json`).
- `src/imgproc/web/app.py` — FastAPI app. Endpoints below.
- `src/imgproc/web/static/` — `index.html` + `reviewer.html` + `demo.html`
  pages, plus their `*.js`/`*.css`. Vanilla — no framework.
- `imgproc.yaml` — global defaults (600×800 canvas, bg threshold 245,
  etc.). `folder.yaml` in any batch overrides for that batch.
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

## Output Layout (per input folder)
```
<batch_folder>/
  <raw images at top level>             ← originals; never relocated by verdicts
  processed/    ← 600×800 white-bg outputs (the ship-list to Pegasus)
  review/       ← low-confidence images flagged for human eye
  skipped/      ← lifestyle/non-white auto-filtered + manually-rejected
  report.html + _report_assets/          ← QA report (kept for debug)
  batch.json                              ← sidecar; reviewer's source of truth
  folder.yaml                             ← per-batch user-edited overrides (optional)
```

`processed/` is the literal ship-list — anything in it is what would go
to Pegasus. `skipped/` is "won't ship" regardless of who decided.

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
update). Pending: M4 (Sheet check / xlsx linter), M5 (visual imgproc-
sort + dupe detection), M6 (Send to Pegasus via Syncthing folder), M7
(Repeat batch with changes), M8 (Pre-buy column-strip). Plus a deferred
UI-polish task to replace native alert/prompt/confirm with themed
modals.

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
