"""Detect edits to files an unattended drafting run must never touch.

The drafting models read untrusted posting text and can write files. A posting that tricked a model
into editing CLAUDE.md, the /apply workflow, the templates or this web app would persist across
every later run, so each unattended job snapshots these files first and fails if any changed.
"""

from __future__ import annotations

from pathlib import Path

from .paths import REPO

PROTECTED_GLOBS = (
    "CLAUDE.md",
    "AGENTS.md",
    ".gitignore",
    ".claude/**/*",
    "templates/**/*",
    "web/*.py",
    "web/api/*.py",
    "web/apply_forms/*.py",
    "web/apply_forms/*.json",
    "web/queue_config.json",
)
_SKIP_PARTS = {"__pycache__", "node_modules"}
_MAX_BYTES = 2_000_000


def snapshot(root: Path | None = None) -> dict[str, bytes]:
    """Map every protected file (repo-relative path) to its current bytes."""
    root = root or REPO
    files: dict[str, bytes] = {}
    for pattern in PROTECTED_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file() or _SKIP_PARTS & set(path.parts):
                continue
            try:
                if path.stat().st_size <= _MAX_BYTES:
                    files[path.relative_to(root).as_posix()] = path.read_bytes()
            except OSError:
                continue
    return files


def changed(before: dict[str, bytes], root: Path | None = None) -> list[str]:
    """Return 'modified: x', 'added: x' or 'removed: x' for every difference since `before`."""
    after = snapshot(root)
    problems = [f"modified: {name}" for name in sorted(before) if name in after and after[name] != before[name]]
    problems += [f"removed: {name}" for name in sorted(before) if name not in after]
    problems += [f"added: {name}" for name in sorted(after) if name not in before]
    return problems


def backup_originals(before: dict[str, bytes], problems: list[str], directory: Path) -> Path:
    """Save the pre-run bytes of modified or removed files so the user can restore them."""
    for entry in problems:
        kind, _, name = entry.partition(": ")
        if kind in {"modified", "removed"} and name in before:
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(before[name])
    return directory
