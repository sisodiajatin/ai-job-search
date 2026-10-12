from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .paths import AGENTS_SKILLS, REPO, SCRAPE_CONFIG

LogFn = Callable[[dict[str, Any]], None]

_ENABLED_RE = re.compile(r"^enabled:\s*(true|false)\b", re.IGNORECASE | re.MULTILINE)


def find_bun() -> str:
    found = shutil.which("bun")
    if found:
        return found
    home = Path.home()
    for candidate in (home / ".bun" / "bin" / "bun.exe", home / ".bun" / "bin" / "bun"):
        if candidate.is_file():
            return str(candidate)
    raise FileNotFoundError(
        "bun was not found on PATH. Install it from https://bun.sh then restart the terminal."
    )


def load_scrape_config() -> dict[str, Any]:
    if not SCRAPE_CONFIG.is_file():
        return {"jobage": 14, "limit": 20, "queries": []}
    return json.loads(SCRAPE_CONFIG.read_text(encoding="utf-8"))


def _frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    if end == -1:
        return ""
    return text[3:end]


def discover_enabled_portals() -> dict[str, Path]:
    """Portal name -> cli.ts path, only if SKILL.md is enabled (missing key = enabled)."""
    found: dict[str, Path] = {}
    if not AGENTS_SKILLS.is_dir():
        return found
    for skill_md in sorted(AGENTS_SKILLS.glob("*/SKILL.md")):
        block = _frontmatter(skill_md.read_text(encoding="utf-8"))
        match = _ENABLED_RE.search(block)
        if match and match.group(1).lower() == "false":
            continue
        cli = skill_md.parent / "cli" / "src" / "cli.ts"
        if cli.is_file():
            found[skill_md.parent.name] = cli
    return found


def _stringify_flag(value: Any) -> str:
    return str(value)


def build_cli_args(
    portal: str,
    query: str,
    portal_flags: dict[str, Any],
    jobage: int,
    limit: int,
) -> list[str]:
    args = ["search", "-q", query, "--jobage", str(jobage), "--limit", str(limit), "--format", "json"]
    if portal == "linkedin-search" and "location" not in portal_flags:
        raise ValueError("linkedin-search requires location")
    reserved = {"query"}
    for key, value in portal_flags.items():
        if key in reserved or value is None or value is False:
            continue
        flag = f"--{key}"
        if value is True:
            args.extend([flag])
        else:
            args.extend([flag, _stringify_flag(value)])
    if portal == "freehire-search":
        args.extend(["--description-format", "text"])
    return args


def _parse_results(stdout: str) -> list[dict[str, Any]]:
    text = stdout.strip()
    if not text:
        return []
    data = json.loads(text)
    if isinstance(data, dict) and isinstance(data.get("results"), list):
        return [row for row in data["results"] if isinstance(row, dict)]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def run_portal_search(
    bun: str,
    cli: Path,
    args: list[str],
    timeout: int = 90,
) -> tuple[list[dict[str, Any]], str | None]:
    env = os.environ.copy()
    bun_dir = str(Path(bun).resolve().parent)
    env["PATH"] = bun_dir + os.pathsep + env.get("PATH", "")
    command = [bun, "run", str(cli), *args]
    try:
        completed = subprocess.run(
            command,
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return [], f"timed out after {timeout}s"
    except OSError as exc:
        return [], str(exc)

    if completed.returncode != 0:
        err = (completed.stderr or completed.stdout or "").strip()
        try:
            parsed = json.loads(completed.stderr or "")
            if isinstance(parsed, dict) and parsed.get("error"):
                err = str(parsed["error"])
        except json.JSONDecodeError:
            pass
        return [], err or f"exit {completed.returncode}"

    try:
        return _parse_results(completed.stdout), None
    except json.JSONDecodeError:
        return [], "CLI did not return JSON"


def planned_calls() -> list[dict[str, Any]]:
    config = load_scrape_config()
    jobage = int(config.get("jobage") or 14)
    limit = int(config.get("limit") or 20)
    portals = discover_enabled_portals()
    calls: list[dict[str, Any]] = []
    for entry in config.get("queries") or []:
        if not isinstance(entry, dict):
            continue
        query = str(entry.get("query") or "").strip()
        if not query:
            continue
        for portal, cli in portals.items():
            portal_flags = entry.get(portal)
            if not isinstance(portal_flags, dict):
                continue
            try:
                args = build_cli_args(portal, query, portal_flags, jobage, limit)
            except ValueError:
                continue
            calls.append(
                {
                    "portal": portal,
                    "query": query,
                    "args": args,
                    "cli": str(cli.relative_to(REPO).as_posix()),
                }
            )
    return calls


def run_scrape(log: LogFn) -> dict[str, int]:
    from .store import merge_listing

    totals = {"added": 0, "existing": 0, "errors": 0, "calls": 0}
    try:
        bun = find_bun()
    except FileNotFoundError as exc:
        log({"type": "error", "message": str(exc)})
        totals["errors"] += 1
        return totals

    log({"type": "log", "message": f"Using bun at {bun}"})
    portals = discover_enabled_portals()
    if not portals:
        log({"type": "error", "message": "No enabled portal CLIs found under .agents/skills/"})
        totals["errors"] += 1
        return totals
    log({"type": "log", "message": "Enabled portals: " + ", ".join(sorted(portals))})

    calls = planned_calls()
    if not calls:
        log({"type": "error", "message": "No scrape calls planned. Check web/scrape_config.json."})
        totals["errors"] += 1
        return totals

    for call in calls:
        portal = call["portal"]
        query = call["query"]
        args = call["args"]
        cli = REPO / call["cli"]
        totals["calls"] += 1
        log(
            {
                "type": "call",
                "portal": portal,
                "query": query,
                "message": f"{portal}: {query}",
            }
        )
        results, error = run_portal_search(bun, cli, args)
        if error:
            totals["errors"] += 1
            log(
                {
                    "type": "result",
                    "portal": portal,
                    "query": query,
                    "added": 0,
                    "existing": 0,
                    "error": error,
                    "message": f"{portal} failed: {error}",
                }
            )
            continue
        added = 0
        existing = 0
        for row in results:
            outcome = merge_listing(row, portal)
            if outcome == "inserted":
                added += 1
            elif outcome == "existing":
                existing += 1
        totals["added"] += added
        totals["existing"] += existing
        log(
            {
                "type": "result",
                "portal": portal,
                "query": query,
                "added": added,
                "existing": existing,
                "error": None,
                "message": f"{portal} “{query}”: {added} new, {existing} already stored ({len(results)} returned)",
            }
        )

    log({"type": "done", **totals, "message": f"Finished. {totals['added']} new listings."})
    return totals


def main_cli() -> int:
    """Allow `python -m api.scrape` for a one-shot scrape without the UI."""
    def _print(event: dict[str, Any]) -> None:
        print(event.get("message") or json.dumps(event), flush=True)

    run_scrape(_print)
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
