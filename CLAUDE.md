# Picasso (imgproc) — Claude Code Context

## What This Is
Intelligent product-image processor for Delicious Display. Takes raw
product-photography folders and produces 600×800 white-background
heroes suitable for Alida's pre-buy sheets + downstream Pegasus ingest.

Standalone tool — Alida runs it on any image folder whenever, not only
on folders destined for Pegasus library ingest. Keep that in mind when
deciding where behaviour should live.

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
- `src/imgproc/cli.py` — `imgproc` CLI entry (folder arg → processed/review/skipped output dirs)
- `src/imgproc/ingest/` — SKU bucketing / matching (`imgproc-sort`)
- `src/imgproc/engine/` — `detect_product`, `compute_group_stats`, `normalize_to_canvas`
- `src/imgproc/report/writer.py` — HTML QA report generator
- `imgproc.yaml` — defaults (600×800 canvas, bg threshold 245, etc.)
- `Picasso.bat` — drag-and-drop wrapper for Alida
- `batches/` — per-run working folders (gitignored)
- `source/` — scratch input folders (gitignored)

## Commands
- `imgproc <folder>` — main resize pass on top-level images
- `imgproc-sort <source-folder> <xlsx>` — bucket raw images by SKU, rename to their SKU code

## Output Layout (per input folder)
```
<source_folder>/
  <raw images at top level>
  processed/    ← resized candidates at 600×800, white bg
  review/       ← outliers flagged for human review
  skipped/      ← filtered (lifestyle / non-white-bg) with reason in name
```

## Handoff to Pegasus
Pegasus's `POST /import/excel` consumes a folder of images alongside an
xlsx. Current expected naming (positional, see Pegasus CLAUDE.md):
- `{product_number}-{image_number}.{ext}` — product-shared
- `{product_number}-{letter}-{image_number}.{ext}` — variant-specific

Picasso's `imgproc-sort` produces SKU-named files (`{SKU}.jpg`,
`{SKU}-b.jpg`, etc.). Reconciliation between positional-numbering
(Pegasus today) and SKU-naming (Picasso) is active work — check the
Pegasus repo's recent commits for the current state before editing
either side.

## When editing both projects at the same time
- Open the **Pegasus** Claude Code session (it's the consumer and has
  the full context). Use `cd ../Picasso` from the shell to peek at
  Picasso source; use `git -C ../Picasso ...` to commit there.
- Avoids session-switching friction without merging the repos.

## Gotchas
- `_find_images` only scans the top level; a `processed/` subdir from a
  previous run won't get re-processed (intentional, otherwise every
  run doubles the output).
- Skip filters run BEFORE group stats so lifestyle shots don't drag
  the median toward huge-"product" scenes.
- Folder-level overrides: drop a `folder.yaml` in the input folder to
  override `imgproc.yaml` defaults for that run only.
