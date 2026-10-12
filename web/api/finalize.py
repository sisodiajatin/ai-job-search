"""Compile drafted sources and run the mechanical checks outside any model sandbox.

A sandboxed model run can be unable to start MiKTeX on Windows, so a job could end
"failed" at Step 5 even though the sources were finished and reviewed. When a model has
completed Steps 0-4 and saved its sources in the checkpoint, the runner calls `finalize` to do the
parts of Step 5 that need no judgment: compile with the active templates' engines, count pages,
and check the PDF text layer. The visual inspection, keyword table and final checklist still need
a model or a person, so the result carries an explicit warning rather than a silent pass.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .paths import COVER_DIR, CV_DIR, REPO

CV_TEMPLATE_GUIDE = ".claude/skills/job-application-assistant/05-cv-templates.md"
ENGINES = ("pdflatex", "lualatex", "xelatex")
STEPS_BEFORE_COMPILE = {0, 1, 2, 3, 4}

# Standing rules from the user: no "recent" opener, no availability or relocation on the CV, and
# no mention of the job-search automation. Narrow patterns on purpose, to avoid false failures.
BANNED_CV = re.compile(
    r"\bavailable to start\b|\bwilling to relocate\b|\brecent (?:ms|m\.s\.|computer|graduate|cs)\b"
    r"|job[- ]search (?:automation|pipeline|framework)",
    re.I,
)
BANNED_COVER = re.compile(r"job[- ]search (?:automation|pipeline|framework)|automates my own job search", re.I)
EMAIL = "jatinsisodiausa@gmail.com"
PHONE = "774-701-4370"


def cv_settings(root: Path | None = None) -> tuple[str, int]:
    """(engine, page limit) for the CV from the active-template block, else the stock moderncv rules."""
    guide = (root or REPO) / CV_TEMPLATE_GUIDE
    try:
        text = guide.read_text(encoding="utf-8")
    except OSError:
        return "lualatex", 2
    block = re.search(r"BEGIN ACTIVE-TEMPLATE.*?END ACTIVE-TEMPLATE", text, re.S)
    if not block:
        return "lualatex", 2
    engine = re.search(r"Compile command:\*\*[^\n]*?\b(pdflatex|lualatex|xelatex)\b", block.group(0))
    pages = re.search(r"Page limit:\*\*\s*exactly\s+(\d+)\s+page", block.group(0))
    return (engine.group(1) if engine else "lualatex"), (int(pages.group(1)) if pages else 2)


def page_count(pdf: Path, log_text: str = "") -> int | None:
    """Pages in `pdf`: ask pdfinfo, else read the TeX log. TeX wraps log lines at 79 characters, so the
    "Output written" sentence is joined across line breaks before it is searched."""
    if pdf.is_file() and shutil.which("pdfinfo"):
        proc = subprocess.run(  # noqa: S603 - fixed tool, our own path
            ["pdfinfo", str(pdf)], capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=30, stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        found = re.search(r"^Pages:\s+(\d+)", proc.stdout, re.M)
        if found:
            return int(found.group(1))
    tail = log_text.split("Output written on", 1)[-1] if "Output written on" in log_text else ""
    found = re.search(r"\((\d+) pages?", tail.replace(chr(10), ""))
    return int(found.group(1)) if found else None


def compile_tex(tex: Path, engine: str, timeout: int = 240) -> tuple[bool, int | str]:
    """Compile `tex` in its folder. Returns (True, page_count) or (False, reason)."""
    if engine not in ENGINES:
        return False, f"unsupported engine {engine!r}"
    if shutil.which(engine) is None:
        return False, f"{engine} is not installed or not on PATH"
    pdf = tex.with_suffix(".pdf")
    before = pdf.stat().st_mtime if pdf.exists() else 0.0
    try:
        subprocess.run(  # noqa: S603 - fixed engine name, tex path chosen by us
            [engine, "-interaction=nonstopmode", tex.name],
            cwd=str(tex.parent), capture_output=True, text=True, timeout=timeout,
            stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return False, f"{engine} timed out after {timeout}s"
    log = tex.with_suffix(".log")
    try:
        log_text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
    pages = page_count(pdf, log_text)
    for extension in (".aux", ".log", ".out"):
        try:
            tex.with_suffix(extension).unlink()
        except OSError:
            pass
    if not pdf.is_file() or pdf.stat().st_mtime <= before or pages is None:
        return False, f"{engine} did not produce a new PDF for {tex.name}"
    return True, pages


def pdf_text(pdf: Path) -> str:
    if shutil.which("pdftotext") is None:
        return ""
    proc = subprocess.run(  # noqa: S603 - fixed tool, our own path
        ["pdftotext", "-enc", "UTF-8", "-layout", str(pdf), "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
        stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return proc.stdout if proc.returncode == 0 else ""


def check_cv(text: str, pages: int, limit: int) -> list[str]:
    problems = []
    if pages != limit:
        problems.append(f"CV is {pages} page(s); the active template requires exactly {limit}")
    if not text.strip():
        return problems + ["CV text layer could not be extracted (is pdftotext installed?)"]
    if "(cid:" in text or "�" in text:
        problems.append("CV text layer has garbled characters")
    if EMAIL not in text:
        problems.append("CV email is not present as literal text")
    if PHONE not in text:
        problems.append("CV phone is not present as literal text")
    if re.search(r"\b(?:19|20)\d{2}\s*[–—]\s*(?:(?:19|20)\d{2}|[A-Z][a-z]{2,8})", text) or re.search(r"[A-Z][a-z]{2} \d{4}\s*[–—]", text):
        problems.append("a CV date range uses an en-dash instead of a plain hyphen")
    banned = BANNED_CV.search(text)
    if banned:
        problems.append(f"CV contains text you ruled out: {banned.group(0)!r}")
    return problems


def check_cover(text: str, pages: int) -> list[str]:
    problems = []
    if pages != 1:
        problems.append(f"cover letter is {pages} pages; it must be exactly 1")
    if not text.strip():
        return problems + ["cover letter text layer could not be extracted"]
    if "�" in text:
        problems.append("cover letter text layer has garbled characters")
    banned = BANNED_COVER.search(text)
    if banned:
        problems.append(f"cover letter contains text you ruled out: {banned.group(0)!r}")
    return problems


def _source(progress: dict[str, Any], key: str, directory: Path, started: float) -> Path | None:
    value = (progress.get("artifact_paths") or {}).get(key)
    if not isinstance(value, str) or not value.endswith(".tex"):
        return None
    path = (REPO / value).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        return None
    return path if path.stat().st_mtime >= started - 5 else None


def finalize(progress: dict[str, Any] | None, started: float) -> dict[str, Any]:
    """Return {"ok": True, cv, cover, score, pages...} or {"ok": False, "reason": ...}.

    Only runs when the checkpoint proves Steps 0-4 (including grounding and the reviewer) finished and
    names both sources written during this job. It never edits a source.
    """
    if not isinstance(progress, dict):
        return {"ok": False, "reason": "no checkpoint to finish from"}
    done = {int(s) for s in progress.get("completed_steps") or [] if isinstance(s, int)}
    if not STEPS_BEFORE_COMPILE <= done:
        return {"ok": False, "reason": "the checkpoint does not show Steps 0-4 as complete"}
    cv_tex = _source(progress, "cv_source", CV_DIR, started)
    cover_tex = _source(progress, "cover_source", COVER_DIR, started)
    if cv_tex is None or cover_tex is None:
        return {"ok": False, "reason": "the checkpoint does not name both fresh source files"}

    engine, limit = cv_settings()
    ok, cv_pages = compile_tex(cv_tex, engine)
    if not ok:
        return {"ok": False, "reason": f"CV compile failed: {cv_pages}"}
    ok, cover_pages = compile_tex(cover_tex, "xelatex")
    if not ok:
        return {"ok": False, "reason": f"cover letter compile failed: {cover_pages}"}

    problems = check_cv(pdf_text(cv_tex.with_suffix(".pdf")), int(cv_pages), limit)
    problems += check_cover(pdf_text(cover_tex.with_suffix(".pdf")), int(cover_pages))
    if problems:
        return {"ok": False, "reason": "mechanical checks failed: " + "; ".join(problems)}

    fit = (progress.get("verification_results") or {}).get("fit") or {}
    score = fit.get("overall") if isinstance(fit.get("overall"), (int, float)) else None
    return {
        "ok": True,
        "cv": cv_tex.with_suffix(".pdf").relative_to(REPO).as_posix(),
        "cover": cover_tex.with_suffix(".pdf").relative_to(REPO).as_posix(),
        "score": score,
        "engine": engine,
    }
