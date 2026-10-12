"""Open the employer's application form in Chrome, fill what is safe, and stop before Submit.

Usage (from the repository root):
    python web/fill_form.py "Authentic"                      # look the listing up by company name
    python web/fill_form.py "<listing url>" --draft          # also draft free-text answers first
    python web/fill_form.py "Authentic" --url "<apply page>" # skip the lookup and use this page
    python web/fill_form.py --observe "<job or apply page>"  # you apply by hand; only page structure is recorded
    python web/fill_form.py "Authentic" --max-steps 8        # walk at most this many application steps

The job must already have a drafted CV and cover letter. Nothing is ever submitted: you review the
form in the browser window and press Submit yourself. If a confirmation page appears afterwards,
the listing is marked applied automatically.
"""

from __future__ import annotations

import argparse
import json
import sys
import subprocess
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent


def run_observe(url: str, minutes: float) -> int:
    """Passive recording session: no clicks, no typing, no uploads, no values saved. See engine/recorder.py."""
    from api.launcher import LaunchError, validate_url
    from apply_forms.engine.recorder import observe

    try:
        url = validate_url(url)
    except LaunchError as err:
        print(err)
        return 1
    result = observe(url, minutes=minutes, log=print)
    print(f"Recorded {result['pages']} page(s).")
    for path in result["files"]:
        print(f"  {path}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("query", help="company name or exact listing URL from seen_jobs.json")
    parser.add_argument("--url", help="application page to use instead of looking it up")
    parser.add_argument("--draft", action="store_true", help="draft answers to free-text questions with Claude first")
    parser.add_argument("--cv", help="exact CV PDF produced by the drafting flow")
    parser.add_argument("--cover", help="exact cover-letter PDF produced by the drafting flow")
    parser.add_argument("--dry-run", action="store_true", help="only look up the job, PDFs and application page; open no browser")
    parser.add_argument("--headless", action="store_true", help="no visible browser (for testing; no wait for confirmation)")
    parser.add_argument("--observe", action="store_true", help="record page structure while you apply by hand (query is the page URL)")
    parser.add_argument("--minutes", type=float, default=90.0, help="how long --observe keeps recording (default 90)")
    parser.add_argument("--max-steps", type=int, default=8, help="how many application steps to walk before stopping")
    args = parser.parse_args()

    if str(WEB_DIR) not in sys.path:
        sys.path.insert(0, str(WEB_DIR))
    if args.observe:
        return run_observe(args.query, args.minutes)
    from api.paths import APPLICATIONS_DIR, REPO
    from api.store import _slug, load_seen, mark_applied, resolve_pdfs
    from apply_forms.draft import draft_answers
    from apply_forms.engine.mapper import MEMORY_PATH
    from apply_forms.fill import run_fill
    from apply_forms.resolve import resolve
    from mark_applied import find_keys

    seen = load_seen()["seen"]
    keys = find_keys(args.query, seen)
    if len(keys) != 1:
        print(f"{len(keys)} listings match {args.query!r}; use the exact listing URL." if keys else f"No listing matches {args.query!r}.")
        return 1
    key = keys[0]
    job = {**seen[key], "key": key}
    company, title = str(job.get("company", "")), str(job.get("title", ""))

    cv_rel, cover_rel = resolve_pdfs(job)
    cv = REPO / cv_rel if cv_rel else None
    cover = REPO / cover_rel if cover_rel else None
    if args.cv or args.cover:
        from apply_flow import checked_pdf

        cv = checked_pdf(args.cv, "cv")
        cover = checked_pdf(args.cover, "cover_letters")
        cv_rel = cv.relative_to(REPO).as_posix()
        cover_rel = cover.relative_to(REPO).as_posix()
    if not cv or not cv.is_file():
        print(f"No drafted CV PDF found for {company}. Draft it first (Apply now, or Run queue).")
        return 1
    print(f"Job: {company} | {title}\n  CV: {cv_rel}\n  Cover letter: {cover_rel or '(none found)'}")

    url = args.url
    if not url:
        found = resolve(company, title)
        if found["status"] == "ambiguous":
            print("Several postings match; re-run with --url. Candidates:")
            for c in found["candidates"]:
                print(f"  {c['title']} | {c['location']} | {c['apply_url']}")
            return 1
        if found["status"] != "found":
            listing = key if str(key).startswith("https://") else ""
            if listing and not args.dry_run:
                from apply_forms.engine.discover import find_apply_link

                print("No board match. Looking for an Apply button on the listing...")
                discovered = find_apply_link(listing, headless=args.headless)
                if discovered["status"] != "found":
                    print(discovered["message"])
                    print("Add the board to web/apply_forms/slugs.json or pass --url.")
                    return 1
                url = discovered["apply_url"]
                print(f"Application page ({discovered['via']}): {url}")
            else:
                print(f"Could not find the employer's application page: {found.get('reason')}\nAdd the board to web/apply_forms/slugs.json or pass --url.")
                return 1
        else:
            url = found["apply_url"]
            print(f"Application page ({found['platform']}, match {found['confidence']}): {url}")
    if not url.startswith("https://"):
        print("Only https:// application pages are supported.")
        return 1

    if args.dry_run:
        print("Dry run: nothing opened.")
        return 0

    profile = {k: v for k, v in json.loads((WEB_DIR / "apply_forms" / "profile_fields.json").read_text(encoding="utf-8")).items() if not k.startswith("_")}
    folder = APPLICATIONS_DIR / _slug(company, title)
    folder.mkdir(parents=True, exist_ok=True)
    answers_path = folder / "form_answers.json"
    answers: dict[str, str] = {}
    if answers_path.is_file():
        try:
            answers = json.loads(answers_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            answers = {}

    def prepare_answers(questions: list[str]) -> dict[str, str]:
        print(f"Drafting {len(questions)} free-text answer(s) while Chrome stays open...")
        (folder / "form_questions.json").write_text(json.dumps(questions, indent=2, ensure_ascii=False), encoding="utf-8")
        cv_tex = (REPO / cv_rel).with_suffix(".tex")
        try:
            drafted = draft_answers(questions, company, title, cv_tex.relative_to(REPO).as_posix() if cv_tex.is_file() else None, REPO)
        except (RuntimeError, subprocess.TimeoutExpired) as err:
            print(f"Could not draft all free-text answers: {err}. Continuing with your details and PDFs; review unanswered questions in Chrome.")
            drafted = {}
        answers.update(drafted)
        answers_path.write_text(json.dumps(answers, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Drafted {len(drafted)} answer(s). Watch Chrome as the application is filled; review before submitting.")
        return drafted

    report = run_fill(
        url,
        profile,
        {"cv": str(cv), "cover": str(cover) if cover else None},
        answers,
        print,
        headless=args.headless,
        prepare_answers=prepare_answers if args.draft else None,
        use_model=args.draft,
        max_steps=args.max_steps,
        memory_path=MEMORY_PATH,
        screenshot=folder / "form_filled.png",
        on_confirmed=lambda: print(json.dumps(mark_applied(key)["row"].get("status"))),
    )
    (folder / "form_fill_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if report.get("note") == "no_form_fields":
        print("\nNOTHING WAS FILLED: no form fields were found on that page. It looks like a job description or sign-in page.")
        print("Click Apply or sign in yourself, then run the filler again with --url set to the form page.")
        print(f"\nReport: {(folder / 'form_fill_report.json').relative_to(REPO).as_posix()}")
        return 2
    print("\nFor you to answer yourself:")
    for item in report["needs_you"]:
        print(f"  - {item['label']}")
    if report["questions"]:
        print("Free-text questions left empty:")
        for item in report["questions"]:
            print(f"  - {item['label']}")
    print(f"\nReport: {(folder / 'form_fill_report.json').relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
