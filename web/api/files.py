from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from .paths import ALLOWED_FILE_PREFIXES, REPO


def resolve_allowed_file(relpath: str) -> Path:
    if not relpath or not relpath.strip():
        raise HTTPException(status_code=400, detail="Missing path")
    normalized = relpath.replace("\\", "/").lstrip("/")
    if any(part == ".." for part in normalized.split("/")):
        raise HTTPException(status_code=400, detail="Invalid path")
    if not any(normalized.startswith(prefix) for prefix in ALLOWED_FILE_PREFIXES):
        raise HTTPException(status_code=403, detail="Path is not in an allowed folder")
    try:
        full = (REPO / normalized).resolve()
        full.relative_to(REPO.resolve())
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid path") from None
    if not full.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return full
