"""Load credentials from a `.env` file (python-dotenv), never committed (.gitignore).

Looked up in this order, first hit wins, existing environment variables are never overridden:
`FIRELINE_ENV_FILE` if set, then `.env` in the current working directory, then `.env` in the
package's repository root and each parent above it (so a worktree under `.worktrees/` finds the main
checkout's file). Values are only ever read into `os.environ`; nothing here prints them.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
_loaded: list[Path] = []


def candidates() -> list[Path]:
    out: list[Path] = []
    explicit = os.environ.get("FIRELINE_ENV_FILE")
    if explicit:
        out.append(Path(explicit).expanduser())
    out.append(Path.cwd() / ".env")
    for d in (ROOT, *ROOT.parents):
        out.append(d / ".env")
    seen: set[Path] = set()
    uniq = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def load_env(force: bool = False) -> Path | None:
    """Load the first existing candidate `.env` (once per process unless `force`). Returns its path."""
    if _loaded and not force:
        return _loaded[0]
    for p in candidates():
        if p.is_file():
            load_dotenv(p, override=False)
            _loaded.append(p)
            return p
    return None


def has_deepfire_credentials() -> bool:
    load_env()
    return bool(os.environ.get("DEEPFIRE_TOKEN") or
                (os.environ.get("DEEPFIRE_CLIENT_ID") and os.environ.get("DEEPFIRE_CLIENT_SECRET")))


def has_anthropic_key() -> bool:
    load_env()
    return bool(os.environ.get("ANTHROPIC_API_KEY"))
