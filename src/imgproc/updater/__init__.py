"""In-app updater: GitHub Releases client + swap-on-restart machinery.

`check_for_update(current)` queries the GitHub Releases API for the latest
tagged release and reports whether it's newer than the running build.
`perform_swap(url)` downloads + stages + spawns a detached swap script,
backing up the current install to %LOCALAPPDATA%\\Picasso\\backups\\ and
relaunching after the copy.

Public-repo assumption — no auth tokens. Asset naming convention:
`picasso-v{X.Y.Z}.zip` containing the unrolled dist/Picasso/ folder.
"""

from .github import UpdateInfo, check_for_update
from .swap import perform_swap, app_data_root

__all__ = ["UpdateInfo", "check_for_update", "perform_swap", "app_data_root"]
