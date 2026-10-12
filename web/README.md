# Local job desk

A localhost website for browsing scraped listings, running LinkedIn/Freehire searches, and opening application PDFs. It does **not** score fit or draft CVs — those stay in chat (`/apply`, `/rank`, `/scrape` with the agent).

Bound to `127.0.0.1` only. Tracker rows, `seen_jobs.json`, the apply queue, and PDFs stay on your machine (already gitignored).

## Pages

- **Dashboard** — pipeline stats, search funnel, tracker breakdown, weekly application trend, follow-up alerts.
- **Today** — what needs you right now: follow-ups that have gone quiet (button **I followed up** records `followed up <date>` in the tracker notes, the same marker `/outcome` counts), drafts ready to send (**Mark submitted**), the apply queue, and up to five fresh high-fit listings you can queue. The sidebar badge counts follow-ups plus drafts waiting.
- **Jobs** — filterable listing table; click a row for a detail drawer with fit notes, strengths/gaps, PDF previews, and queue/submit actions. Also runs the portal search with a live log.
- **Pipeline** — kanban board over the tracker with the same stages `/outcome` and `/gmail-sync` write: `applied`, `interview`, `offer`, `hired`, `rejected`, `no_response`, `withdrawn`, `offer_declined`, plus a **Prepared** column for drafts. Spellings such as `No response` or `Offer declined` are normalised, and a status the board does not recognise appears in an **Other** column instead of vanishing. Each card shows the fit score, the next follow-up date (10 days after last contact, none once the application is closed or two follow-ups have been sent) and a CV/Letter link. Drag cards between stages or use each card's picker.
- **Library** — browses `documents/applications/` archives, prepared packets, and compiled draft PDFs.

No frontend dependencies beyond React/Router; charts are hand-rolled CSS/SVG.

## Run

From the repository root:

```powershell
pip install -r web/requirements.txt
cd web/frontend
npm install
npm run build
cd ../..
python web/run.py
```

The Dashboard opens in your browser automatically (or visit [http://127.0.0.1:8787](http://127.0.0.1:8787)). Use `python web/run.py --no-browser` to skip that.

### Live UI reload (optional)

In one terminal:

```powershell
python web/run.py
```

In another:

```powershell
cd web/frontend
npm install
npm run dev
```

Then open [http://127.0.0.1:5173](http://127.0.0.1:5173). Vite proxies `/api` to the FastAPI process.

## Apply now (drafts, then fills the employer's form)

On **Jobs**, open a listing and click **Apply now**. A PowerShell window runs `python web/apply_flow.py <job URL>` and works in two steps. **Step 1 evaluates the fit only** (`/apply --evaluate-only`, nothing is drafted) and prints the fit evaluation in that window: overall score and verdict, the eligibility gate with the posting's own wording, the five dimension scores, strengths, gaps and a recommendation. It then asks **`Draft your CV and cover letter for this role, then open the employer's form? [y/N]`**. Anything other than `y` or `yes` (including closing the window) drafts nothing and opens nothing, and you are never held to the usual score floor of 60 or the eligibility gate: the decision is yours. **Step 2, only after you say yes,** runs the same workflow with `--approved`, reusing the saved evaluation: it tailors the CV and cover letter, runs the reviewer, compiles them and verifies the PDFs. The evaluation is capped by `evaluation_budget_usd` (default 3; a real evaluation cost about $0.60 and the cap is split between possible Claude attempts) and drafting by `per_job_budget_usd`; the timeout comes from `web/queue_config.json`.

Only after successful drafting and checking that **both exact result PDFs exist**, it automatically runs the form filler: opens Chrome on the employer's application page, uploads those PDFs, fills profile details, and drafts free-text answers. Failed or skipped drafting never opens the form. Review the documents and completed form, answer the highlighted questions, and submit yourself. The flow never presses Submit. Supported portals and manual fields are described below under **Fill form**. If page resolution or filling fails, the console reports the error; the drafted documents remain available for manual use or the separate **Fill form** button.

Windows only, and `claude` must be on PATH. The endpoint only accepts requests from this site (Origin, Host and `X-Job-Desk` header checks) and only `https://` listing URLs without shell metacharacters; the URL goes to PowerShell through an environment variable, never inside the command text. **Restart `python web/run.py` after updating** so the new endpoint is loaded.

## Run queue (unattended drafts)

With jobs in the **Apply queue** panel on Jobs, click **Run queue (drafts only)**. The server works through the queue one job at a time, starting a headless Claude Code session for each: `claude -p "/apply <url> --auto --result <file>"`. In `--auto` mode `/apply` does not ask questions: it skips a job that fails the eligibility gate (citizenship, clearance, explicit F-1/OPT exclusion) or scores below `score_floor` (60), and otherwise drafts the CV and cover letter, runs the reviewer, compiles and checks them. Results show as chips (drafted / skipped / failed) with the reason, and a job that was drafted is marked `prepared`. **Nothing is ever submitted.** Submit yourself, then tell Claude "applied".

Settings are in [`queue_config.json`](queue_config.json): `score_floor`, `per_job_budget_usd` (default 5), `per_job_timeout_minutes` (25), `max_consecutive_failures` (3, then the run stops), and an optional `model`. **Stop** kills the current job and leaves it in the queue.

### Run queue and fill forms (drafts, then the employer forms, one at a time)

Next to **Run queue (drafts only)** there is **Run queue and fill forms**. It does the same unattended drafting, then walks the jobs it drafted, in queue order, and for each one runs the same filler as **Fill form** with that job's own two PDFs (`fill_form.py <url> --draft --cv <pdf> --cover <pdf>`): it opens the employer's application page in the filler's Chrome profile, uploads the tailored CV and cover letter, fills your details, and drafts the free-text answers. **It never presses Submit.** Skipped and failed jobs have no PDFs, so they get no form.

- **One form at a time.** Chrome runs on a single profile (`web/.browser-profile/`), so forms cannot be filled in parallel. The slow, model-driven drafting for the whole queue finishes first and unattended; then the first form opens and fills, and **the next one opens when you close that window** (or its confirmation page marks the job applied). Plan for one quick review per job.
- **Same stops as Fill form.** A sign-in or create-account page, an email code, a CAPTCHA, a voluntary-disclosure page or a review page pause that form with a banner until you act. Work authorization, sponsorship, demographic, salary, start-date, consent and relocation questions are outlined in orange and left for you.
- **A bad form never blocks the rest.** Each result chip gets a second chip: `form: filled`, `form: nothing filled` (a job description or login wall; use Fill form on that job once you are past it) or `form: failed` with the reason on hover. A failed form does not count toward `max_consecutive_failures` and never un-drafts a job.
- **Time limit.** `fill_forms_timeout_minutes` in `queue_config.json` (default 75). A form still open after that is closed and the run moves on.
- **Stop** kills the current form's browser and leaves the remaining jobs unopened (their drafts are kept).

Restart `python web/run.py` after updating so the new endpoint and button are loaded.

### Auto-queue: let the agent pick what to draft (high fit first, then medium)

`python web/auto_queue.py` puts the best-fitting open jobs on the apply queue so you do not have to pick them by hand. It **only queues**: drafting, form filling and
submitting stay with the queue runner and you. `--dry-run` shows the picks and changes nothing; `--limit N` and `--min-fit high|medium` override the settings for one run.

- **Who gets picked:** jobs `/rank` already rated **high or medium** that are still open (`new`, `ranked` or `evaluated`) and are not queued, tracked, prepared or submitted, did not fail the
  eligibility gate, were not skipped by an earlier automatic run, and were not tried automatically in the last `retry_after_days`. **High before medium; newest first within each.**
  Unscored listings are never guessed: run `/rank` first.
- **Caps** (spend control; each draft costs real model usage), in `queue_config.json` under `"auto_queue"`: `per_run_limit` (default 3 added per run), `max_queue_size` (default 5 in the queue
  at once), `retry_after_days` (default 3), `min_fit` (`medium`, or `high` for high only).
- **`verified_eligibility_only`** (default off): queue only jobs whose eligibility is already recorded as `pass`. Most ranked jobs have no eligibility recorded yet, so turning this on shrinks
  the pool a lot; with it off, the queue runner's own gate (citizenship, clearance, explicit F-1/OPT exclusion) skips a bad fit, after spending an evaluation on it.
- **`enabled`** (default off) only controls whether a scheduler does this by itself. With Hermes (see `web/hermes_job/`) the nightly job then queues first and drafts afterwards, and its summary
  starts with an "Auto-queued N job(s)" list. Running the command by hand always works.

### Run the queue from the command line (for a scheduler or another agent)

`python web/queue_cli.py` does what the dashboard buttons do, without the browser: it drafts every queued job (`--fill-forms` also opens each form afterwards), streams the log, and ends with a one-line summary (`--json` adds a machine-readable last line with counts and one entry per job: company, title, status, score, reason, form status; no document paths or answers). `--status` lists the queue and runs nothing. Ctrl+C stops the current job. It never submits.

Exit codes: `0` finished (jobs may still be skipped or failed: read the summary), `1` could not start (empty queue, `claude` not on PATH), `3` the queue is already running elsewhere, `130` interrupted.

**One queue run at a time.** The dashboard, this command and any scheduler share a cross-process lock (`job_scraper/auto_results/<hash>.lock`). A second runner does nothing and says so, instead of grabbing the same first job and dequeuing something the other is still drafting.

`--fill-forms` opens Chrome windows, so it has to run in your logged-in desktop session. A background scheduler usually cannot show windows there; schedule the drafting alone and fill the forms from the dashboard when you are at the desk.

### Usage limits and interrupted drafts

Application runs for the same listing are exclusive across processes, so repeated Apply clicks cannot overwrite each other's drafts. If Claude reports a usage or rate limit, the job stops with the message **"Claude reached its usage limit"**, keeps whatever it already wrote, and does not retry elsewhere. Run it again after the limit resets (your Claude plan's allowance, not a bill, unless you enabled extra usage). Budget-cap errors, login errors, a user Stop and a timeout also stop the job and are reported as such.

Each job saves a progress checkpoint under the gitignored `job_scraper/auto_results/`. If a model finished the drafting and review but could not compile, the runner compiles the saved sources itself (see `api/finalize.py`) and records the job as drafted with a warning to open both PDFs. Result files written by Windows PowerShell with a UTF-8 byte-order mark are accepted.

`per_job_budget_usd` (default 9) caps one drafting run and `evaluation_budget_usd` (default 3) caps one fit evaluation; free-text form-answer drafting is capped at $1. These are Claude Code's own cost estimates, a safety ceiling for runaway runs.

### Protected files

Drafting models read untrusted posting text and can write files, so before every unattended job the runner snapshots `CLAUDE.md`, `AGENTS.md`, `.gitignore`, everything under `.claude/` and `templates/`, the web app's Python files and `queue_config.json`. If any of them changed, added or removed when the job ends, the job is marked **failed**, its result is discarded (nothing is recorded as drafted), the originals are saved under `job_scraper/auto_results/protected_backup/<run>/`, and the reason lists the files. If you edited one of those files yourself while a job was running, ignore the failure and run it again.

Each run spends model usage (drafting plus a reviewer agent per job), so queue only jobs you want drafted. The headless sessions get `acceptEdits` and a short tool allowlist, not full permissions, because they read untrusted posting text. Start and Stop only accept requests from this site, like **Apply now**.

## Fill form (opens the employer's application form, never submits)

After a job has been drafted, open it on **Jobs** and click **Fill form** (or run `python web/fill_form.py "<company>" --draft`). It:

1. Finds the employer's own application page through the public Ashby, Greenhouse and Lever job-board APIs (`apply_forms/resolve.py`; add unusual boards to `apply_forms/slugs.json`).
2. Opens your installed Chrome visibly, with a separate profile in `web/.browser-profile/` (so a login you do there is remembered). The same window stays open during answer drafting, filling and review.
3. Uploads the job's CV and cover-letter PDFs and types your details from `apply_forms/profile_fields.json` (edit that file if anything is wrong). Watch it live: a progress panel shows the current field or upload, and the page scrolls to and highlights each action with a brief pause so you can follow it.
4. With `--draft`, drafts answers to free-text questions with a read-only Claude call grounded in your profile, saved as `documents/applications/<job>/form_answers.json`. The questions are passed as quoted data, never as instructions.
5. **Never fills** work authorization, sponsorship, demographic, salary, start-date, consent or relocation questions. They are outlined in orange on the page and listed in the report. **It never clicks Submit,** and it never types a password.
6. Multi-step forms are walked with Next, Continue, Save and continue, Apply manually and Start application, up to `--max-steps` (default 8). It stops, with a banner, on a sign-in or create-account page, an email-code page, a CAPTCHA, a voluntary-disclosure / self-identify / consent page, a review page, a validation error, or a page it does not understand. The window stays open and the walk resumes when that page changes, so you can sign in, enter the code, solve the CAPTCHA or answer the disclosure yourself.
7. The progress panel changes to **Ready for your review**. Chrome remains open for up to 60 minutes while you review, answer the highlighted questions, and submit. If a confirmation page appears, the job is marked applied automatically. The report lists filled, left for you, and skipped counts, plus per-step screenshots.

Honest limit: this is a general filler with optional site hints, not a promise that every employer site will work. Login walls, email verification, CAPTCHAs, bot detection and a site's own rules still stop some applications. When that happens the banner says so. LinkedIn Easy Apply is not automated. Needs `pip install -r web/requirements.txt` (Playwright, which drives the Chrome already on this machine; no browser download).

## Record a site (`--observe`): how new sites get supported

The form filler learns new sites from real pages, not guesses. To record one, run:

```powershell
python web/fill_form.py --observe "https://<the job or apply page>"
```

A Chrome window opens in the filler profile (logins persist there). **You apply by hand**: sign in, create the account, enter the codes, click through every page. The recorder only watches; it never clicks, types or submits. For each distinct page it saves one line to `web/apply_forms/observed/<site>.jsonl` (gitignored): the URL path (no query), the page title and headings, field labels, kinds and required flags, `<select>` and radio options, button names, and the `data-automation-id` / `name` / `id` hints. **It never saves anything you type, never describes a password field (it only notes one exists), and masks your own profile values, email addresses and phone numbers.** A green panel in the window says it is recording. Close the window, or wait the default 90 minutes (`--minutes`), to stop.

Personal values the filler may type live in `web/apply_forms/profile_fields.json` (gitignored). Fill in `address_line1`, `postal_code` and `preferred_name` (leave the last blank if it is the same as your first name). City, state, country, phone country code and phone type are there too. Nothing in that file is sent anywhere except into the employer's own form.

Site hints live in `web/apply_forms/engine/hints/`. They are optional. Workday is the first pack (the Apply button and the manual-apply choice). The filler still runs on a site with no pack. After a real application, missed fields become fixtures and, when a recording supports it, a hint. `web/apply_forms/memory.json` (gitignored) remembers a label that was already mapped to a profile field so the next form does not have to ask again.

## Queue a job, then `/apply` in chat

1. On **Jobs**, click **Queue for /apply**.
2. In this chat, run `/apply queue` (or `/apply` with no argument). The agent takes **only the first** queued URL, removes it from `job_scraper/apply_queue.json`, and drafts the CV and cover letter.
3. After you actually submit the application, click **Submitted**. That writes a row to `job_search_tracker.csv`, sets the listing to submitted, and creates a stub `outcome.md` (and copies the CV/cover `.tex` if those archive names are missing) so later scrapes and `/rank` skip that company+role.

**Submitted is the apply-moment write.** Chat `/outcome` is for interview stages, silence, offers, and follow-up drafts. It does not need to run just to log that you sent the application.

## Search listings vs `/scrape` in chat

| | **Search listings** (this site) | **`/scrape` in chat** |
|---|---|---|
| What it does | Runs the enabled portal CLIs (`linkedin-search`, `freehire-search`) and inserts unseen URLs into `job_scraper/seen_jobs.json` | Same CLIs, plus a quick LLM fit rating and presentation in chat |
| New rows | `status: new`, `fit: unscored` | Agent-assigned `high` / `medium` / `low` |
| Existing rows | Left unchanged (ranked, skipped, expired, notes, scores) | Same idea — does not clobber history |
| Fit / rank | Not computed | Use `/rank` in chat to batch-score `new` jobs; scores then show in the table |

Queries live in [`scrape_config.json`](scrape_config.json) (14-day window, 20 results per call). Edit that file to change keywords. Danish demo portals stay off unless their skill `enabled` is true.

## LinkedIn volume

The LinkedIn CLI reads public job pages and is for personal use. Keep the configured `--limit` and recency; do not raise volume for bulk collection.
