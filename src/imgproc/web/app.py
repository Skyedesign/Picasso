"""FastAPI app driving the local imgproc UI.

Endpoints are intentionally minimal — the UI is a thin shell around the same CLI
and config machinery that runs from the terminal. Processing happens in a
background thread and is polled via `/api/jobs/{id}`.

Path safety: every `batch_name` received from the client is validated to ensure
the resolved path stays inside `BATCHES_ROOT`. No path traversal.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import threading
import uuid
from io import StringIO
from pathlib import Path
from typing import Any

import yaml
from click.testing import CliRunner
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..cli import IMAGE_EXTS, main as cli_main
from ..config import Config, find_project_root

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

    target.mkdir()
    imported = 0
    try:
        for p in source.iterdir():
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                dest = target / p.name
                if body.move:
                    shutil.move(str(p), str(dest))
                else:
                    shutil.copy2(p, dest)
                imported += 1
    except Exception:
        # Clean up a half-imported batch so the user isn't left with a weird state.
        shutil.rmtree(target, ignore_errors=True)
        raise

    if imported == 0:
        target.rmdir()
        raise HTTPException(400, "no image files (.jpg, .png, .webp, .bmp) found in source folder")

    return {"name": name, "imported": imported, "moved": body.move}


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
        }

    def run() -> None:
        args = [str(folder), "--no-open-report"]
        if body.tolerance is not None:
            args += ["--tolerance", str(body.tolerance)]
        if body.target_ratio is not None:
            args += ["--target-ratio", str(body.target_ratio)]
        if body.dry_run:
            args.append("--dry-run")

        runner = CliRunner()
        try:
            result = runner.invoke(cli_main, args, catch_exceptions=False)
            status = "done" if result.exit_code == 0 else "error"
            log = result.output
        except Exception as e:  # defensive — shouldn't happen for well-formed inputs
            status = "error"
            log = f"Exception: {e}"

        with _jobs_lock:
            _jobs[job_id]["status"] = status
            _jobs[job_id]["log"] = log
            if status == "done" and (folder / "report.html").exists():
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


def run_server(host: str = "127.0.0.1", port: int = 8765) -> None:
    """Entry point used by the `imgproc-ui` script."""
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
