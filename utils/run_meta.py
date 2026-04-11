"""
utils/run_meta.py
─────────────────
Lightweight helpers that capture run-level metadata: the current git
commit SHA and a fingerprint of the active config file.  Both values
are stored in run_history so that any run can be reproduced exactly.

Functions
─────────
    get_git_sha()              → 8-char short SHA of HEAD, or 'unknown'
    get_config_hash(path)      → 8-char MD5 hex digest of the file, or 'unknown'

Design notes
────────────
- Both helpers are intentionally *defensive*: any OS/filesystem/git
  error returns the sentinel string 'unknown' rather than raising.
  The pipeline must never abort just because it cannot read a git SHA.
- MD5 is used for config hashing because we only need a short change-
  detector fingerprint, not a cryptographic guarantee.  The 8-char
  truncation keeps the stored value compact while still making
  accidental collisions practically impossible for config files.
- subprocess is called with check=False and a short timeout so the
  pipeline is never blocked by a slow git index or missing binary.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


def get_git_sha() -> str:
    """
    Return the short 8-character git SHA of HEAD.

    Uses ``git rev-parse --short HEAD`` so the value matches what you
    see in ``git log --oneline``.

    Returns:
        8-char hex string, e.g. ``'a1b2c3d4'``, or ``'unknown'`` when
        the working directory is not inside a git repository, git is not
        installed, or any other error occurs.
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if proc.returncode == 0:
            sha = proc.stdout.strip()
            return sha if sha else "unknown"
    except Exception:  # noqa: BLE001  (FileNotFoundError, TimeoutExpired, OSError, …)
        pass
    return "unknown"


def get_config_hash(config_path: str | Path) -> str:
    """
    Return an 8-character MD5 hex digest of *config_path*'s raw bytes.

    The digest changes whenever any byte of the file changes, making it
    a reliable change-detector for YAML/TOML config files.

    Args:
        config_path: Path to the config file (absolute or relative).

    Returns:
        First 8 characters of the MD5 hex digest, e.g. ``'d41d8cd9'``,
        or ``'unknown'`` if the file cannot be read for any reason.
    """
    try:
        raw = Path(config_path).read_bytes()
        return hashlib.md5(raw).hexdigest()[:8]  # noqa: S324 (MD5 is fine for change detection)
    except Exception:  # noqa: BLE001
        return "unknown"
