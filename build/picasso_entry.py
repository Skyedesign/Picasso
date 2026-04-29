"""PyInstaller entry for the packaged `picasso.exe`.

Mirrors the `imgproc-ui` console-script entry: just calls run_server. The
script lives in build/ rather than src/ because it's a build artifact —
real Python code stays under src/imgproc.
"""

from imgproc.web.app import run_server


if __name__ == "__main__":
    run_server()
