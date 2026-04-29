# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Picasso.
#
# --onedir build (not --onefile): Pillow + numpy + uvicorn push a one-file
# bundle past 100 MB and ~3 s startup; --onedir starts instantly and is a
# normal folder for the in-app updater to swap.
#
# `collect_all('PIL')` defeats Pillow plugin auto-discovery quirks (TIFF,
# libwebp). scipy is excluded — `imagehash` lazy-imports it for non-phash
# hash modes; we only use phash.

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

PROJECT_ROOT = Path(SPECPATH).parent.resolve()
ENTRY = str(PROJECT_ROOT / "build" / "picasso_entry.py")
SRC = PROJECT_ROOT / "src"

# Pull every PIL module + data file. PyInstaller's default Pillow hook
# misses some plugin modules on Windows (TIFF/WebP especially).
pil_binaries, pil_datas, pil_hiddenimports = collect_all("PIL")


a = Analysis(
    [ENTRY],
    pathex=[str(SRC)],
    binaries=pil_binaries,
    datas=[
        # Web UI assets — FastAPI's StaticFiles mount + index/reviewer/demo
        # HTML are loaded from disk at request time, so they have to be on
        # disk in the bundle. Mapping mirrors the package layout.
        (str(SRC / "imgproc" / "web" / "static"), "imgproc/web/static"),
        # Jinja template for the QA report.
        (str(SRC / "imgproc" / "report" / "template.html.j2"), "imgproc/report"),
    ] + pil_datas,
    hiddenimports=[
        # uvicorn's runtime imports its protocol/loop handlers by string
        # name; PyInstaller's static analysis misses them.
        "uvicorn.lifespan",
        "uvicorn.lifespan.on",
        "uvicorn.protocols",
        "uvicorn.protocols.http",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.loops",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        # openpyxl for imgproc-sort xlsx parsing (carried into the bundle
        # so a future "drop xlsx" UI works without separate installs).
        "openpyxl",
        "openpyxl.styles",
        "openpyxl.styles.differential",
        "et_xmlfile",
    ] + pil_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # imagehash imports scipy lazily for whash/etc.; we only call phash,
        # so dropping scipy saves ~80 MB in the bundle.
        "scipy",
        # Test/dev-only deps that PyInstaller would otherwise pull via
        # transitive imports from openpyxl/Pillow.
        "pytest",
        "tkinter",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,  # onedir mode — binaries live alongside the exe
    name="picasso",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX trips Defender heuristics on small dev shops; skip
    console=True,  # keep the console window so Alida can see status + Ctrl+C
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Picasso",  # dist/Picasso/ is the install dir layout
)
