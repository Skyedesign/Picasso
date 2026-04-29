"""GitHub Releases API client for the in-app updater.

The repo is public so no auth token is required. Failures (network down,
rate limit, malformed JSON) all collapse into `UpdateInfo(has_update=False)`
so the UI never crashes on a bad lookup — at worst the banner doesn't show.
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

GITHUB_API_LATEST = "https://api.github.com/repos/Skyedesign/Picasso/releases/latest"


@dataclass
class UpdateInfo:
    current_version: str
    latest_version: str
    has_update: bool
    download_url: str | None = None
    release_notes: str = ""
    release_url: str = ""
    error: str = ""


def _normalize(v: str) -> tuple[int, int, int]:
    """'v0.4.0' or '0.4.0' or 'v0.4' → (0, 4, 0). Non-numeric segments
    sort to 0 so a malformed tag can't crash compare."""
    parts = v.strip().lstrip("vV").split(".")
    out: list[int] = []
    for p in parts[:3]:
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    while len(out) < 3:
        out.append(0)
    return (out[0], out[1], out[2])


def _is_newer(latest: str, current: str) -> bool:
    return _normalize(latest) > _normalize(current)


def check_for_update(current_version: str, timeout: float = 5.0) -> UpdateInfo:
    """Hit GitHub's latest-release endpoint and report. Returns an
    UpdateInfo even on failure (with `has_update=False` and `error` set)
    so callers don't need try/except."""
    info = UpdateInfo(
        current_version=current_version,
        latest_version=current_version,
        has_update=False,
    )
    try:
        req = urllib.request.Request(GITHUB_API_LATEST, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"picasso-updater/{current_version}",
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except Exception as e:
        info.error = f"{type(e).__name__}: {e}"
        return info

    info.latest_version = (data.get("tag_name") or current_version).lstrip("vV")
    info.release_notes = data.get("body") or ""
    info.release_url = data.get("html_url") or ""
    info.has_update = _is_newer(info.latest_version, current_version)

    # Pick the first asset whose name looks like our zip convention.
    # Matches `picasso-v0.4.0.zip`, `picasso-0.4.zip`, etc.
    for asset in data.get("assets") or []:
        name = (asset.get("name") or "").lower()
        if name.startswith("picasso") and name.endswith(".zip"):
            info.download_url = asset.get("browser_download_url")
            break

    return info
