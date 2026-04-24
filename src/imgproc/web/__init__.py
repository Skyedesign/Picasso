"""Local web UI for imgproc. Served via `imgproc-ui` on http://127.0.0.1:8765."""

from .app import run_server

__all__ = ["run_server"]
