"""FastAPI app driving the local imgproc UI.

Endpoints are intentionally minimal — the UI is a thin shell around the same CLI
and config machinery that runs from the terminal. Processing happens in a
background thread and is polled via `/api/jobs/{id}`.

Path safety: every `batch_name` received from the client is validated to ensure
the resolved path stays inside `BATCHES_ROOT`. No path traversal.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import uuid
import webbrowser
from io import BytesIO, StringIO
from pathlib import Path
from typing import Any, Literal

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from PIL import Image

from ..batch_meta import (
    BatchMeta,
    ImageRow,
    ImageVerdict,
    now_iso,
    read_meta,
    update_verdict,
    update_verdicts_bulk,
    write_meta,
)
from ..cli import IMAGE_EXTS, process_folder
from ..config import Config, find_project_root, load_config
from ..engine import detect_product, normalize_to_canvas
from ..engine.background import detect_background
from ..output import status_subfolder
from ..updater import check_for_update, perform_swap

STATIC = Path(__file__).parent / "static"
_PROJECT_ROOT = find_project_root()
BATCHES_ROOT = (_PROJECT_ROOT / "batches").resolve()
CONFIG_PATH = _PROJECT_ROOT / "imgproc.yaml"
BATCHES_ROOT.mkdir(exist_ok=True)

# Conservative: alphanumerics, dash, underscore, space. Prevents path traversal and
# OS-reserved characters on Windows.
_NAME_RE = re.compile(r"^[A-Za-z0-9 _\-]+$")

app = FastAPI(title="imgproc")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
# Expose batches dir so the browser can load report.html and its assets directly,
# and so we can show original images as thumbnails in the UI.
app.mount("/batches", StaticFiles(directory=BATCHES_ROOT, html=True), name="batches")


def _resolve_batch(name: str) -> Path:
    if not _NAME_RE.match(name):
        raise HTTPException(400, "invalid batch name")
    folder = (BATCHES_ROOT / name).resolve()
    try:
        folder.relative_to(BATCHES_ROOT)
    except ValueError:
        raise HTTPException(400, "path escapes batches root")
    if not folder.is_dir():
        raise HTTPException(404, "batch not found")
    return folder


# ─── Pages ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/demo", response_class=HTMLResponse)
def demo_page() -> str:
    """Standalone Demo Resizer at its own URL — for "open in new tab" so Alida
    can run the tool alongside the main UI without modal context-switching."""
    return (STATIC / "demo.html").read_text(encoding="utf-8")


@app.get("/reviewer/{name}", response_class=HTMLResponse)
def reviewer_page(name: str) -> str:
    """Visual reviewer for a single batch. Validates the name to match the
    batch-folder rules; the actual data is fetched client-side from
    /api/batches/{name}/state. Serving the same HTML for any valid batch
    keeps caching simple."""
    if not _NAME_RE.match(name):
        raise HTTPException(400, "invalid batch name")
    return (STATIC / "reviewer.html").read_text(encoding="utf-8")


# ─── Batches ──────────────────────────────────────────────────────────────

@app.get("/api/batches")
def list_batches() -> dict:
    items = []
    for folder in sorted(BATCHES_ROOT.iterdir()):
        if not folder.is_dir():
            continue
        n_images = sum(
            1 for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        )
        has_report = (folder / "report.html").exists()
        processed_count = 0
        review_count = 0
        if (folder / "processed").is_dir():
            processed_count = sum(
                1 for p in (folder / "processed").iterdir()
                if p.suffix.lower() in IMAGE_EXTS
            )
        if (folder / "review").is_dir():
            review_count = sum(
                1 for p in (folder / "review").iterdir()
                if p.suffix.lower() in IMAGE_EXTS
            )
        items.append({
            "name": folder.name,
            "image_count": n_images,
            "has_report": has_report,
            "processed_count": processed_count,
            "review_count": review_count,
        })
    return {"root": str(BATCHES_ROOT), "batches": items}


class NewBatch(BaseModel):
    name: str


@app.post("/api/batches")
def create_batch(body: NewBatch) -> dict:
    name = body.name.strip()
    if not _NAME_RE.match(name):
        raise HTTPException(400, "name may only contain letters, numbers, dash, underscore, space")
    target = BATCHES_ROOT / name
    if target.exists():
        raise HTTPException(409, "batch already exists")
    target.mkdir()
    return {"name": name}


_SOURCE_MAX_DEPTH = 3  # how deep under source/ to walk when populating the dropdown


@app.get("/api/source-folders")
def list_source_folders() -> dict:
    """Enumerate folders under source/ that contain image files, so the UI can
    show Alida a picker instead of requiring her to paste a path."""
    source_root = (_PROJECT_ROOT / "source").resolve()
    if not source_root.is_dir():
        return {"root": str(source_root), "exists": False, "folders": []}

    folders = []

    def walk(path: Path, depth: int) -> None:
        if depth > _SOURCE_MAX_DEPTH:
            return
        try:
            entries = list(path.iterdir())
        except (PermissionError, OSError):
            return
        image_count = sum(
            1 for p in entries
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        )
        if image_count > 0:
            folders.append({
                "path": str(path),
                "relative": str(path.relative_to(source_root)) or ".",
                "image_count": image_count,
            })
        for p in entries:
            if p.is_dir() and not p.name.startswith("."):
                walk(p, depth + 1)

    walk(source_root, 0)
    folders.sort(key=lambda f: f["relative"].lower())
    return {"root": str(source_root), "exists": True, "folders": folders}


class ImportRequest(BaseModel):
    name: str
    source_path: str
    move: bool = False  # if True, move the source files; otherwise copy


@app.post("/api/batches/import")
def import_folder(body: ImportRequest) -> dict:
    # Target: must be a fresh batch under BATCHES_ROOT.
    name = body.name.strip()
    if not _NAME_RE.match(name):
        raise HTTPException(400, "name may only contain letters, numbers, dash, underscore, space")
    target = BATCHES_ROOT / name
    if target.exists():
        raise HTTPException(409, "batch already exists — pick a different name")

    # Source: any folder on the filesystem (read-only from our side). Strip the
    # surrounding quotes Windows' "Copy as path" adds, and expand ~ for convenience.
    src_str = body.source_path.strip().strip('"').strip("'")
    if not src_str:
        raise HTTPException(400, "source folder path is required")
    source = Path(src_str).expanduser()
    if not source.exists():
        raise HTTPException(400, f"source folder not found: {source}")
    if not source.is_dir():
        raise HTTPException(400, f"source path is not a folder: {source}")

    # Prevent importing from inside BATCHES_ROOT itself — the only legitimate case
    # is cross-folder reorganization, which should use the existing batch-rename flow
    # (which doesn't exist yet; ask the user before copying inside the tree).
    try:
        source.resolve().relative_to(BATCHES_ROOT.resolve())
        raise HTTPException(400, "source is inside the batches folder; import from an outside location")
    except ValueError:
        pass  # good — source is outside BATCHES_ROOT

    # Threshold matches the lifestyle filter default — anything below this will be
    # routed to skipped/ at process time, so flagging it now lets Alida pre-cull.
    non_white_threshold = 0.85

    target.mkdir()
    imported = 0
    non_white: list[str] = []
    try:
        for p in source.iterdir():
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                dest = target / p.name
                if body.move:
                    shutil.move(str(p), str(dest))
                else:
                    shutil.copy2(p, dest)
                imported += 1
                # Check the copied file's background. Cheap — samples 4 corners only.
                try:
                    with Image.open(dest) as img:
                        bg = detect_background(img)
                        if bg.purity < non_white_threshold:
                            non_white.append(p.name)
                except Exception:
                    pass  # skip malformed images silently — they'll surface at process time
    except Exception:
        # Clean up a half-imported batch so the user isn't left with a weird state.
        shutil.rmtree(target, ignore_errors=True)
        raise

    if imported == 0:
        target.rmdir()
        raise HTTPException(400, "no image files (.jpg, .png, .webp, .bmp) found in source folder")

    return {
        "name": name,
        "imported": imported,
        "moved": body.move,
        "non_white_count": len(non_white),
        "non_white_files": non_white,
    }


@app.delete("/api/batches/{name}")
def delete_batch(name: str) -> dict:
    # Destructive — recursively removes the batch folder and everything it contains
    # (originals, processed/, review/, report, assets). The `_resolve_batch` helper
    # ensures `name` can't escape BATCHES_ROOT via path traversal.
    folder = _resolve_batch(name)
    shutil.rmtree(folder)
    return {"ok": True, "deleted": name}


@app.post("/api/batches/{name}/open")
def open_in_explorer(name: str) -> dict:
    folder = _resolve_batch(name)
    # Windows-only for now; the user's environment is Win11.
    if sys.platform == "win32":
        subprocess.Popen(["explorer", str(folder)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(folder)])
    else:
        subprocess.Popen(["xdg-open", str(folder)])
    return {"ok": True}


# ─── Config ───────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config() -> dict:
    if CONFIG_PATH.exists():
        data = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    else:
        data = Config().model_dump()
    # Round-trip through Config to return validated + defaulted values.
    cfg = Config(**data)
    out = cfg.model_dump()
    out["output_canvas"] = list(out["output_canvas"])
    return out


@app.post("/api/config")
def save_config(body: dict[str, Any]) -> dict:
    # Validate via the same pydantic schema the CLI uses.
    cfg = Config(**body)
    data = cfg.model_dump()
    data["output_canvas"] = list(data["output_canvas"])
    CONFIG_PATH.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return {"ok": True}


# ─── Processing jobs ──────────────────────────────────────────────────────

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


class ProcessRequest(BaseModel):
    name: str
    tolerance: float | None = None
    target_ratio: float | None = None
    dry_run: bool = False


@app.post("/api/process")
def start_processing(body: ProcessRequest) -> dict:
    folder = _resolve_batch(body.name)

    job_id = uuid.uuid4().hex
    with _jobs_lock:
        _jobs[job_id] = {
            "status": "running",
            "log": "",
            "batch": body.name,
            "report_url": None,
            "progress": {"phase": "starting", "current": 0, "total": 0},
        }

    overrides: dict = {}
    if body.tolerance is not None:
        overrides["tolerance_mad"] = body.tolerance
    if body.target_ratio is not None:
        overrides["target_ratio"] = body.target_ratio

    def _log(msg: str) -> None:
        with _jobs_lock:
            cur = _jobs[job_id]["log"]
            _jobs[job_id]["log"] = (cur + "\n" + msg) if cur else msg

    def _progress(p: dict) -> None:
        # Snapshot under the lock — UI polls and reads atomically.
        with _jobs_lock:
            _jobs[job_id]["progress"] = dict(p)

    def run() -> None:
        try:
            result = process_folder(
                folder,
                cli_overrides=overrides,
                progress=_progress,
                log=_log,
                dry_run=body.dry_run,
            )
            ok = bool(result.get("ok"))
        except Exception as e:
            _log(f"Exception: {e}")
            ok = False

        with _jobs_lock:
            _jobs[job_id]["status"] = "done" if ok else "error"
            if ok and (folder / "report.html").exists():
                _jobs[job_id]["report_url"] = f"/batches/{body.name}/report.html"

    threading.Thread(target=run, daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "job not found")
        return dict(job)


# ─── Demo Resizer & batch image picker ───────────────────────────────────
# Demo Resizer is the "tune the fill ratio on one image" tool, ported from
# the Pegasus-embedded modal. Standalone Picasso calls the engine in-process
# (no subprocess to imgproc-hero), wraps in to_thread to keep handlers
# responsive while Pillow holds the GIL on a big TIFF decode.

def _render_with_overrides(
    img: Image.Image,
    cfg: Config,
    *,
    target_ratio: float,
    canvas_size: tuple[int, int],
    bbox: tuple[int, int, int, int] | None = None,
    centroid: tuple[float, float] | None = None,
    source_path: Path | None = None,
    padding_pct: float = 0.0,
) -> tuple[bytes, dict]:
    """Detect + apply optional bbox/centroid overrides + normalize.

    Returns (JPEG bytes, summary dict). Shared by Demo Resizer preview,
    reviewer live-preview, and the verdict re-run path so override
    semantics stay identical across all three.

    `padding_pct` defaults to 0 because every caller of this helper is in
    a per-image override workflow where target_ratio is the user's
    explicit request — letting the configured padding constrain the result
    would create a dead zone at the top of the slider. The CLI's group-
    batch path is unaffected; it calls `normalize_to_canvas` directly with
    `cfg.padding_pct`.
    """
    det = detect_product(source_path or Path(":preview:"), img, bg_threshold=cfg.bg_threshold)
    if det.bbox[2] - det.bbox[0] <= 0 or det.bbox[3] - det.bbox[1] <= 0:
        raise HTTPException(422, "no product detected")

    # bbox override: must be in-bounds with positive area.
    if bbox is not None:
        l, t, r, b = bbox
        if not (0 <= l < r <= det.width and 0 <= t < b <= det.height):
            raise HTTPException(422, "bbox out of bounds")
        det.bbox = (l, t, r, b)
        det.occupied_ratio = ((r - l) * (b - t)) / (det.width * det.height)
        if centroid is None:
            det.centroid = ((l + r) / 2.0, (t + b) / 2.0)

    # centroid override: a single click in the original sets where the
    # product lands on the canvas. Forces the mask-centroid path so
    # `det.centroid` is honoured regardless of the global recenter pref.
    recenter_on_mask = cfg.recenter
    if centroid is not None:
        cx, cy = centroid
        if not (0 <= cx <= det.width and 0 <= cy <= det.height):
            raise HTTPException(422, "centroid out of bounds")
        det.centroid = (float(cx), float(cy))
        recenter_on_mask = True
    if bbox is not None and centroid is None:
        recenter_on_mask = True

    normalized = normalize_to_canvas(
        det,
        target_ratio=target_ratio,
        canvas_size=canvas_size,
        padding_pct=padding_pct,
        max_upscale=cfg.max_upscale,
        recenter_on_mask_centroid=recenter_on_mask,
    )
    out = BytesIO()
    normalized.save(out, format="JPEG", quality=92)
    return out.getvalue(), {
        "occupied_ratio": det.occupied_ratio,
        "confidence": det.confidence,
        "bg_purity": det.bg.purity,
        "bg_is_white": det.bg.is_white,
    }


class PreviewRequest(BaseModel):
    image_b64: str
    ext: str = "jpg"
    target_ratio: float = Field(ge=0.0, le=1.0)
    canvas_size: tuple[int, int] | None = None  # falls back to imgproc.yaml's value
    bbox: tuple[int, int, int, int] | None = None
    centroid: tuple[float, float] | None = None


@app.post("/api/picasso/preview")
async def picasso_preview(body: PreviewRequest) -> dict:
    """Render a single image with overrides and return a JPEG as base64."""
    try:
        src_bytes = base64.b64decode(body.image_b64)
    except Exception:
        raise HTTPException(400, "bad base64")
    if not src_bytes:
        raise HTTPException(400, "empty image")

    cfg = load_config(CONFIG_PATH)
    canvas_size = tuple(body.canvas_size) if body.canvas_size else cfg.output_canvas
    ext = body.ext.lower().lstrip(".") or "jpg"

    def _render() -> bytes:
        try:
            img = Image.open(BytesIO(src_bytes))
            img.load()
        except Exception as e:
            raise HTTPException(400, f"cannot read image: {e}")
        out_bytes, _ = _render_with_overrides(
            img, cfg,
            target_ratio=body.target_ratio,
            canvas_size=canvas_size,
            bbox=body.bbox,
            centroid=body.centroid,
            source_path=Path(f"preview.{ext}"),
        )
        return out_bytes

    out_bytes = await asyncio.to_thread(_render)
    return {
        "image_b64": base64.b64encode(out_bytes).decode("ascii"),
        "ratio": body.target_ratio,
        "canvas_size": list(canvas_size),
    }


class BatchPreviewRequest(BaseModel):
    target_ratio: float = Field(ge=0.0, le=1.0)
    canvas_size: tuple[int, int] | None = None
    bbox: tuple[int, int, int, int] | None = None
    centroid: tuple[float, float] | None = None


@app.post("/api/batches/{name}/preview/{filename}")
async def picasso_preview_from_batch(name: str, filename: str, body: BatchPreviewRequest) -> dict:
    """Live preview for the reviewer's re-run panel: load the original from
    the batch's top-level, apply overrides, return the rendered JPEG as
    base64. Avoids round-tripping the image through base64 over the wire
    when it's already on disk in the batch."""
    folder = _resolve_batch(name)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")
    src_path = folder / filename
    if not src_path.is_file():
        raise HTTPException(404, f"source image not at top level: {filename}")

    cfg = load_config(CONFIG_PATH)
    canvas_size = tuple(body.canvas_size) if body.canvas_size else cfg.output_canvas

    def _render() -> bytes:
        try:
            img = Image.open(src_path)
            img.load()
        except Exception as e:
            raise HTTPException(400, f"cannot read image: {e}")
        out_bytes, _ = _render_with_overrides(
            img, cfg,
            target_ratio=body.target_ratio,
            canvas_size=canvas_size,
            bbox=body.bbox,
            centroid=body.centroid,
            source_path=src_path,
        )
        return out_bytes

    out_bytes = await asyncio.to_thread(_render)
    return {
        "image_b64": base64.b64encode(out_bytes).decode("ascii"),
        "ratio": body.target_ratio,
        "canvas_size": list(canvas_size),
    }


_SUBFOLDERS = {"processed", "review", "skipped"}


@app.get("/api/batches/{name}/images")
def list_batch_images(name: str) -> dict:
    """List image files in a batch grouped by location, for the Demo Resizer
    "pick from batch" picker. Returns top-level (originals), processed/, and
    review/ filenames only — no full paths (the picker calls the thumbnail
    endpoint by basename + sub)."""
    folder = _resolve_batch(name)
    out: dict = {"top": []}
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            out["top"].append(p.name)
    for sub in ("processed", "review"):
        sub_dir = folder / sub
        out[sub] = []
        if sub_dir.is_dir():
            for p in sorted(sub_dir.iterdir(), key=lambda x: x.name.lower()):
                if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                    out[sub].append(p.name)
    return out


@app.get("/api/batches/{name}/thumbs/{filename}")
def batch_thumbnail(name: str, filename: str, w: int = 200, sub: str = "") -> Response:
    """Small JPEG thumbnail for the picker grid. Pillow's `draft` mode
    decodes JPEGs at a downsampled resolution — substantially faster than a
    full decode when the source is multi-MP."""
    folder = _resolve_batch(name)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")
    if sub and sub not in _SUBFOLDERS:
        raise HTTPException(400, "invalid subfolder")
    if w <= 0 or w > 1024:
        raise HTTPException(400, "invalid width")

    file_path = (folder / sub / filename) if sub else (folder / filename)
    if not file_path.is_file():
        raise HTTPException(404, "image not found")

    img = Image.open(file_path)
    if img.format == "JPEG":
        img.draft("RGB", (w * 2, w * 2))
    img.thumbnail((w, w * 4), Image.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, format="JPEG", quality=80)
    return Response(content=buf.getvalue(), media_type="image/jpeg")


# ─── Visual reviewer state ────────────────────────────────────────────────
# State endpoint returns the sidecar batch.json verbatim if present, else
# synthesises a best-effort view from folder contents. The latter exists so
# legacy batches (processed before the sidecar landed) still render in the
# reviewer — a re-process will replace the synthetic view with the real
# sidecar. `has_sidecar` lets the UI flag the difference.

@app.get("/api/batches/{name}/state")
def batch_state(name: str) -> dict:
    folder = _resolve_batch(name)
    meta = read_meta(folder)
    if meta is not None:
        return {"has_sidecar": True, **meta.model_dump()}
    return _synthesise_state(name, folder)


def _synthesise_state(name: str, folder: Path) -> dict:
    """Build a minimal state view for a batch with no sidecar.

    Images are listed with status inferred from the subfolder they sit in.
    No detection metrics — those would need a re-run. Verdicts default to
    None. Stats are None: there's no group reference without re-running.
    """
    images: list[dict] = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            # Original at top-level — status unknown until processed.
            images.append(ImageRow(
                name=p.name,
                status="unprocessed",
                occupied_ratio=0.0,
                confidence=0.0,
                bg_is_white=False,
                bg_purity=0.0,
                output_subfolder=None,
                output_filename=None,
            ).model_dump())
    for sub, status in (("processed", "within-tolerance"), ("review", "review"), ("skipped", "skipped-unknown")):
        sub_dir = folder / sub
        if not sub_dir.is_dir():
            continue
        for p in sorted(sub_dir.iterdir(), key=lambda x: x.name.lower()):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                # If a top-level original with the same name already exists in
                # `images`, upgrade its status from "unprocessed" instead of
                # adding a duplicate row.
                existing = next((r for r in images if r["name"] == p.name and r["status"] == "unprocessed"), None)
                if existing:
                    existing["status"] = status
                    existing["output_subfolder"] = sub
                    existing["output_filename"] = p.name
                else:
                    images.append(ImageRow(
                        name=p.name,
                        status=status,
                        occupied_ratio=0.0,
                        confidence=0.0,
                        bg_is_white=False,
                        bg_purity=0.0,
                        output_subfolder=sub,
                        output_filename=p.name,
                    ).model_dump())
    return {
        "has_sidecar": False,
        "schema_version": 1,
        "batch_name": name,
        "last_run_timestamp": None,
        "last_run_config": None,
        "stats": None,
        "images": images,
    }


# ─── File-motion helpers for verdict-driven moves ─────────────────────────
# Reject moves the file to skipped/; un-reject restores it to where Picasso
# originally put it (read from the row's `status` field). Centralised here
# so both single-image and bulk verdicts behave identically.

def _current_output_subfolder(folder: Path, filename: str) -> str | None:
    """Where does this image's OUTPUT copy currently live?

    Top-level originals are NOT output copies — they're preserved by
    Picasso's pipeline and verdict-driven moves never touch them. We only
    look at processed/, review/, skipped/.
    """
    for sub in _SUBFOLDERS:
        if (folder / sub / filename).is_file():
            return sub
    return None


def _infer_status_from_location(folder: Path, filename: str) -> str:
    """For legacy batches with no sidecar row, guess what status Picasso
    would have stamped — used to synthesise the `status` field on the
    first verdict so a later un-reject knows where to put the file back.

    Prefers the output-copy location (most informative); falls back to
    "unprocessed" if there's only a top-level original, or "unknown" if
    the file isn't in the batch at all.
    """
    sub = _current_output_subfolder(folder, filename)
    if sub == "processed": return "within-tolerance"
    if sub == "review":    return "review"
    if sub == "skipped":   return "skipped-unknown"
    if (folder / filename).is_file():
        return "unprocessed"
    return "unknown"


def _move_between_subfolders(folder: Path, filename: str, target_sub: str) -> bool:
    """Move the OUTPUT copy of this image into `target_sub`.

    No-op if the file is already there, or if there's no output copy yet
    (i.e., the image only exists at top-level — verdicts don't relocate
    originals). Returns True if a move actually happened.
    """
    src_sub = _current_output_subfolder(folder, filename)
    if src_sub is None or src_sub == target_sub:
        return False
    src = folder / src_sub / filename
    dst = folder / target_sub / filename
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        # Defensive: clobber any stale destination so the invariant holds.
        try: dst.unlink()
        except OSError: pass
    src.replace(dst)
    return True


def _apply_verdict_motion(
    folder: Path,
    filename: str,
    new_decision: str,
    prior_decision: str | None,
    prior_status: str | None,
) -> str | None:
    """Effect file motion based on the verdict transition. Returns the new
    output_subfolder (or None if no motion happened)."""
    if new_decision == "rejected":
        if _move_between_subfolders(folder, filename, "skipped"):
            return "skipped"
        # Already in skipped/ or only at top-level — record verdict, no
        # location change.
        return None
    if new_decision == "accepted" and prior_decision == "rejected":
        # Un-reject: restore from the sidecar's `status` field.
        target = status_subfolder(prior_status or "")
        if target and target != "skipped":
            if _move_between_subfolders(folder, filename, target):
                return target
    # accept-on-non-rejected, rerun (handled separately), no-op
    return None


# ─── Reviewer verdict + single-image re-run ───────────────────────────────
# One endpoint, three decisions:
#   accepted: record only — the image is good as-is
#   rejected: record only — the image won't ship downstream (filter later)
#   rerun:    re-process this image with user overrides (ratio / canvas /
#             bbox / centroid), write to processed/, clean up stale copies
#             in review/ or skipped/, update sidecar status accordingly.

class VerdictRequest(BaseModel):
    decision: Literal["accepted", "rejected", "rerun"]
    # Re-run overrides — meaningful only when decision == "rerun"
    target_ratio: float | None = Field(default=None, ge=0.0, le=1.0)
    canvas_size: tuple[int, int] | None = None
    bbox: tuple[int, int, int, int] | None = None
    centroid: tuple[float, float] | None = None
    note: str = ""


@app.post("/api/batches/{name}/verdict/{filename}")
async def post_verdict(name: str, filename: str, body: VerdictRequest) -> dict:
    folder = _resolve_batch(name)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")

    # Validate filename actually belongs to this batch (top-level original or
    # any output subfolder). Without this, a verdict on a typo'd name would
    # silently create a stub row in the sidecar.
    in_batch = (folder / filename).is_file() or any(
        (folder / sub / filename).is_file() for sub in _SUBFOLDERS
    )
    if not in_batch:
        raise HTTPException(404, f"image not in batch: {filename}")

    if body.decision != "rerun":
        # accept/reject path: may move the file (reject → skipped/; accept on
        # a previously-rejected image → restore). Verdict + any location
        # change persisted in one sidecar write.
        meta = read_meta(folder)
        prior_decision = None
        prior_status = None
        if meta:
            for row in meta.images:
                if row.name == filename:
                    if row.verdict:
                        prior_decision = row.verdict.decision
                    prior_status = row.status
                    break
        # First-touch on a legacy batch (no row in sidecar): infer the
        # status from the file's current location so a later un-reject
        # has somewhere to restore to.
        if prior_status is None:
            prior_status = _infer_status_from_location(folder, filename)

        new_subfolder = _apply_verdict_motion(
            folder, filename, body.decision, prior_decision, prior_status
        )

        verdict = ImageVerdict(
            decision=body.decision,
            note=body.note,
            timestamp=now_iso(),
        )
        meta = meta or BatchMeta(batch_name=name)
        found = False
        for row in meta.images:
            if row.name == filename:
                row.verdict = verdict
                if new_subfolder is not None:
                    row.output_subfolder = new_subfolder
                    row.output_filename = filename
                found = True
                break
        if not found:
            meta.images.append(ImageRow(
                name=filename,
                status=prior_status,
                occupied_ratio=0.0,
                confidence=0.0,
                bg_is_white=False,
                bg_purity=0.0,
                output_subfolder=new_subfolder,
                output_filename=filename if new_subfolder else None,
                verdict=verdict,
            ))
        write_meta(folder, meta)
        return {
            "ok": True,
            "decision": body.decision,
            "moved_to": new_subfolder,
        }

    # Re-run path: original at top-level → fresh detect → apply overrides →
    # normalize → write to processed/. Engine work goes to a thread because
    # Pillow holds the GIL during decode and a 12 MP source can stall the
    # event loop otherwise.
    src_path = folder / filename
    if not src_path.is_file():
        raise HTTPException(404, f"source image not at top level: {filename}")

    cfg = load_config(CONFIG_PATH)
    canvas_size = tuple(body.canvas_size) if body.canvas_size else cfg.output_canvas

    # target_ratio resolution: explicit > sidecar's group target > config default > 0.65 fallback.
    target_ratio = body.target_ratio
    if target_ratio is None:
        existing = read_meta(folder)
        if existing and existing.stats:
            target_ratio = float(existing.stats.target_ratio)
        elif isinstance(cfg.target_ratio, (int, float)):
            target_ratio = float(cfg.target_ratio)
        else:
            target_ratio = 0.65

    def _render() -> tuple[bytes, dict]:
        try:
            img = Image.open(src_path)
            img.load()
        except Exception as e:
            raise HTTPException(400, f"cannot read image: {e}")
        det = detect_product(src_path, img, bg_threshold=cfg.bg_threshold)

        # bbox override: must be in-bounds with positive area.
        if body.bbox is not None:
            l, t, r, b = body.bbox
            if not (0 <= l < r <= det.width and 0 <= t < b <= det.height):
                raise HTTPException(422, "bbox out of bounds")
            det.bbox = (l, t, r, b)
            det.occupied_ratio = ((r - l) * (b - t)) / (det.width * det.height)
            if body.centroid is None:
                # Recenter on the new bbox's geometric center.
                det.centroid = ((l + r) / 2.0, (t + b) / 2.0)

        # centroid override: a single click in the original sets where the
        # product lands on the canvas. Forces the mask-centroid path so
        # `det.centroid` is honoured regardless of the global recenter pref.
        recenter_on_mask = cfg.recenter
        if body.centroid is not None:
            cx, cy = body.centroid
            if not (0 <= cx <= det.width and 0 <= cy <= det.height):
                raise HTTPException(422, "centroid out of bounds")
            det.centroid = (float(cx), float(cy))
            recenter_on_mask = True
        # Same logic when only bbox was overridden — we want the recomputed
        # bbox-center to actually be used as the anchor.
        if body.bbox is not None and body.centroid is None:
            recenter_on_mask = True

        normalized = normalize_to_canvas(
            det,
            target_ratio=target_ratio,
            canvas_size=canvas_size,
            padding_pct=cfg.padding_pct,
            max_upscale=cfg.max_upscale,
            recenter_on_mask_centroid=recenter_on_mask,
        )
        out = BytesIO()
        normalized.save(out, format="JPEG", quality=92)
        return out.getvalue(), {
            "occupied_ratio": det.occupied_ratio,
            "confidence": det.confidence,
            "bg_purity": det.bg.purity,
            "bg_is_white": det.bg.is_white,
        }

    out_bytes, det_summary = await asyncio.to_thread(_render)

    # Write to processed/ (overwrites any existing processed copy).
    out_path = folder / "processed" / filename
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out_bytes)

    # Clean up stale review/ or skipped/ copies — invariant: an image lives
    # in at most one output subfolder.
    for sub in ("review", "skipped"):
        stale = folder / sub / filename
        if stale.exists():
            try:
                stale.unlink()
            except OSError:
                pass  # best-effort; sidecar is the source of truth anyway

    # Sidecar update: row's status & output now reflect the re-run; verdict
    # captures the user's overrides for traceability.
    meta = read_meta(folder) or BatchMeta(batch_name=name)
    verdict = ImageVerdict(
        decision="rerun",
        target_ratio=target_ratio,
        bbox=body.bbox,
        centroid=body.centroid,
        canvas_size=list(canvas_size),
        note=body.note,
        timestamp=now_iso(),
    )
    found = False
    for row in meta.images:
        if row.name == filename:
            row.status = "within-tolerance"
            row.output_subfolder = "processed"
            row.output_filename = filename
            row.occupied_ratio = det_summary["occupied_ratio"]
            row.confidence = det_summary["confidence"]
            row.bg_purity = det_summary["bg_purity"]
            row.bg_is_white = det_summary["bg_is_white"]
            row.verdict = verdict
            found = True
            break
    if not found:
        meta.images.append(ImageRow(
            name=filename,
            status="within-tolerance",
            occupied_ratio=det_summary["occupied_ratio"],
            confidence=det_summary["confidence"],
            bg_is_white=det_summary["bg_is_white"],
            bg_purity=det_summary["bg_purity"],
            output_subfolder="processed",
            output_filename=filename,
            verdict=verdict,
        ))
    write_meta(folder, meta)

    return {
        "ok": True,
        "decision": "rerun",
        "output_path": "processed/" + filename,
        "occupied_ratio": det_summary["occupied_ratio"],
        # b64 lets the UI swap the preview without a second round-trip.
        "image_b64": base64.b64encode(out_bytes).decode("ascii"),
    }


@app.delete("/api/batches/{name}/verdict/{filename}")
def delete_verdict(name: str, filename: str) -> dict:
    """Clear an image's verdict and undo any verdict-driven file motion.

    - Was rejected → restore file from skipped/ to its original location
      (read from `status`). The image returns to the No-verdict queue.
    - Was accepted → metadata-only clear; file stays where it was.
    - Was rerun → only the verdict tag is cleared. The rerun's output
      remains in processed/ because the original processed file was
      overwritten and is unrecoverable; a fresh CLI run on the batch
      would replace it.
    """
    folder = _resolve_batch(name)
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "invalid filename")
    in_batch = (folder / filename).is_file() or any(
        (folder / sub / filename).is_file() for sub in _SUBFOLDERS
    )
    if not in_batch:
        raise HTTPException(404, f"image not in batch: {filename}")

    meta = read_meta(folder)
    if meta is None:
        return {"ok": True, "had_verdict": False, "moved_to": None}

    target_row = next((r for r in meta.images if r.name == filename), None)
    if target_row is None or target_row.verdict is None:
        return {"ok": True, "had_verdict": False, "moved_to": None}

    prior_decision = target_row.verdict.decision
    new_subfolder: str | None = None
    if prior_decision == "rejected":
        # Symmetric with un-reject (accept-after-reject): restore from
        # `status` to the original output subfolder.
        target = status_subfolder(target_row.status or "")
        if target and target != "skipped":
            if _move_between_subfolders(folder, filename, target):
                new_subfolder = target

    target_row.verdict = None
    if new_subfolder is not None:
        target_row.output_subfolder = new_subfolder
        target_row.output_filename = filename
    write_meta(folder, meta)
    return {"ok": True, "had_verdict": True, "moved_to": new_subfolder}


class BulkVerdictRequest(BaseModel):
    decision: Literal["accepted", "rejected"]
    filenames: list[str] = Field(default_factory=list, min_length=1, max_length=5000)
    note: str = ""


@app.post("/api/batches/{name}/verdict-bulk")
def post_verdict_bulk(name: str, body: BulkVerdictRequest) -> dict:
    """Apply the same accept/reject verdict to many images in one round-trip.

    Re-run isn't supported here — each re-run would need its own bbox /
    centroid / target-ratio, which doesn't generalise. Bulk is only useful
    when the verdict itself doesn't carry per-image overrides.

    Reject moves files into skipped/; un-accept (accept on a previously-
    rejected image) restores from the row's status field. All file motion
    happens before the sidecar write so a crash mid-bulk leaves disk and
    sidecar consistent (writes are atomic; moves are too on Win+POSIX).
    """
    folder = _resolve_batch(name)

    meta = read_meta(folder)
    prior_by_name: dict[str, tuple[str | None, str | None]] = {}
    if meta:
        for row in meta.images:
            prior_by_name[row.name] = (
                row.verdict.decision if row.verdict else None,
                row.status,
            )

    # Validate, then perform any file motion. Accumulate per-file results
    # so the sidecar update at the end is one write.
    motions: list[tuple[str, str | None, str]] = []  # (filename, new_subfolder, captured_status)
    errors: list[dict] = []
    ts = now_iso()
    for fname in body.filenames:
        if "/" in fname or "\\" in fname or ".." in fname:
            errors.append({"filename": fname, "error": "invalid filename"})
            continue
        in_batch = (folder / fname).is_file() or any(
            (folder / sub / fname).is_file() for sub in _SUBFOLDERS
        )
        if not in_batch:
            errors.append({"filename": fname, "error": "not in batch"})
            continue
        prior_decision, prior_status = prior_by_name.get(fname, (None, None))
        if prior_status is None:
            prior_status = _infer_status_from_location(folder, fname)
        new_subfolder = _apply_verdict_motion(
            folder, fname, body.decision, prior_decision, prior_status
        )
        motions.append((fname, new_subfolder, prior_status))

    if motions:
        meta = meta or BatchMeta(batch_name=name)
        by_name = {row.name: row for row in meta.images}
        for fname, new_subfolder, prior_status in motions:
            verdict = ImageVerdict(
                decision=body.decision,
                note=body.note,
                timestamp=ts,
            )
            if fname in by_name:
                by_name[fname].verdict = verdict
                if new_subfolder is not None:
                    by_name[fname].output_subfolder = new_subfolder
                    by_name[fname].output_filename = fname
            else:
                meta.images.append(ImageRow(
                    name=fname,
                    status=prior_status,
                    occupied_ratio=0.0,
                    confidence=0.0,
                    bg_is_white=False,
                    bg_purity=0.0,
                    output_subfolder=new_subfolder,
                    output_filename=fname if new_subfolder else None,
                    verdict=verdict,
                ))
        write_meta(folder, meta)
    return {"ok": True, "applied": len(motions), "errors": errors}


class ApplyPresetRequest(BaseModel):
    target_ratio: float | Literal["auto"] | None = None
    canvas_size: tuple[int, int] | None = None


@app.post("/api/batches/{name}/apply-preset")
def apply_preset(name: str, body: ApplyPresetRequest) -> dict:
    """Write target_ratio and/or canvas_size to the batch's folder.yaml.

    `_resolve_config` in cli.py merges folder.yaml on top of imgproc.yaml at
    process time, so the next run on this batch uses these values without any
    other plumbing. Other folder.yaml fields are preserved.
    """
    folder = _resolve_batch(name)
    folder_yaml = folder / "folder.yaml"
    data: dict = {}
    if folder_yaml.exists():
        data = yaml.safe_load(folder_yaml.read_text(encoding="utf-8")) or {}

    if body.target_ratio is not None:
        data["target_ratio"] = body.target_ratio
    if body.canvas_size is not None:
        data["output_canvas"] = list(body.canvas_size)

    # Round-trip through the same Pydantic schema the CLI uses, so an invalid
    # combo (e.g., target_ratio = 1.5) fails here rather than at process time.
    base = Config().model_dump()
    base.update(data)
    try:
        Config(**base)
    except Exception as e:
        raise HTTPException(422, f"invalid preset: {e}")

    folder_yaml.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return {"ok": True, "folder_yaml": str(folder_yaml), "saved": data}


# ─── Server entry ─────────────────────────────────────────────────────────

# ─── Updater ──────────────────────────────────────────────────────────────
# Public-repo GitHub Releases lookup. The check endpoint is hit on every
# page load by the UI banner; install fires on the user's click. Frozen-
# build only (no swap path makes sense in a dev `pip install -e .` setup).

@app.get("/api/updates/check")
def updates_check() -> dict:
    """Return current vs latest version + download URL. Errors surface as
    {has_update: false, error: "..."} so the UI silently no-ops on a bad
    network."""
    from .. import __version__  # local import keeps CLI import-light
    info = check_for_update(__version__)
    return {
        "current_version": info.current_version,
        "latest_version": info.latest_version,
        "has_update": info.has_update,
        "download_url": info.download_url,
        "release_notes": info.release_notes,
        "release_url": info.release_url,
        "error": info.error,
        "is_frozen": bool(getattr(sys, "frozen", False)),
    }


@app.post("/api/updates/install")
def updates_install() -> dict:
    """Kick off the swap. Responds to the client first, then schedules a
    self-exit so the detached swap script can take over without a port
    conflict on relaunch."""
    from .. import __version__
    if not getattr(sys, "frozen", False):
        raise HTTPException(503, "in-app updater only works in packaged builds")

    info = check_for_update(__version__)
    if not info.has_update:
        raise HTTPException(409, f"no update: latest is {info.latest_version}")
    if not info.download_url:
        raise HTTPException(404, "no compatible asset in latest release")

    swap_bat = perform_swap(info.download_url)
    # Defer exit so the response makes it back to the browser.
    threading.Timer(1.0, lambda: os._exit(0)).start()
    return {
        "ok": True,
        "from_version": info.current_version,
        "to_version": info.latest_version,
        "swap_script": str(swap_bat),
    }


def _is_port_taken(host: str, port: int) -> bool:
    """Return True if something is already listening on host:port.

    Uses connect-and-check (not bind) so we don't briefly hold the port
    ourselves — avoids a TOCTOU window where uvicorn would race to bind
    the same port a moment later.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        s.connect((host, port))
        return True
    except (ConnectionRefusedError, socket.timeout, OSError):
        return False
    finally:
        s.close()


def _open_browser_delayed(url: str, delay_seconds: float = 1.5) -> None:
    """Open the user's browser to `url` after a short delay so uvicorn
    has time to bind. Runs in a daemon thread; failures are silent."""
    def _do() -> None:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    t = threading.Timer(delay_seconds, _do)
    t.daemon = True
    t.start()


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Entry point for `imgproc-ui` and the packaged `picasso.exe`.

    Single-instance: if the port is already bound (Picasso already
    running), open the browser to the existing instance and exit instead
    of crashing with a bind error. On a fresh start, spawn a delayed
    browser-open so the user lands on the UI without an extra click.
    """
    url = f"http://{host}:{port}/"
    if _is_port_taken(host, port):
        print(f"Picasso is already running at {url} — opening your browser.")
        try:
            webbrowser.open(url)
        except Exception:
            pass
        return

    _open_browser_delayed(url)
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="info")
