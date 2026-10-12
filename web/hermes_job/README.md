# Nightly queue drafting with Hermes Agent

A Hermes cron job in **`--no-agent`** mode runs `nightly_queue.py` on a schedule. No language model is involved: Hermes just runs this one script and
delivers its output, so nothing a job posting says can change what runs.

What the script does: runs `python web/queue_cli.py --json` (the same drafting as the dashboard's **Run queue (drafts only)**), then prints counts and one
line per job. Empty queue = no output = Hermes stays silent. It never uses `--fill-forms`, never submits, and never prints raw logs.

## Optional: queue jobs automatically first

Set `"auto_queue": {"enabled": true, ...}` in `web/queue_config.json` and the same job first runs `python web/auto_queue.py --json` (high fit before medium, capped by `per_run_limit` and
`max_queue_size`), then drafts what is in the queue. The summary lists what was auto-queued. A problem while queueing is reported but never stops the drafting run. Try `python web/auto_queue.py --dry-run`
first to see what it would pick. Leave `enabled` false and nothing changes.

## What Hermes must never be given

- Permission to edit files in this repo, or to run anything other than this script.
- `hermes import-agent claude-code` (it would copy `CLAUDE.md`, which holds your whole candidate profile, into Hermes and its memory).
- The ability to press Submit on anything.

## Install (once)

1. Create the stub that points Hermes at this repo's script (so the script lives in git and there is one copy to maintain). In PowerShell:

   ```powershell
   $dir = "$env:LOCALAPPDATA\hermes\scripts"
   New-Item -ItemType Directory -Force $dir | Out-Null
   Set-Content "$dir\job_queue_nightly.py" 'import runpy; runpy.run_path(r"D:\ai-job-search\web\hermes_job\nightly_queue.py", run_name="__main__")'
   ```

2. Create the job, **paused** so nothing runs until you decide (change the time if you like; the machine and Hermes' gateway must be running then):

   ```powershell
   & "$env:LOCALAPPDATA\hermes\bin\hermes.exe" cron create "0 10 * * *" --no-agent --script job_queue_nightly.py --name "Job queue drafts" --deliver local --paused
   ```

3. When you want it on: `hermes cron resume <job id>` (`hermes cron list` shows the id). To try it once now: `hermes cron run <job id>`.

Read results with `hermes cron runs`. Add `--deliver telegram` (after pairing a Telegram bot) to get the summary on your phone.

## Why drafts only

`--fill-forms` opens Chrome windows, which need your logged-in desktop session; a background scheduler usually cannot show them. Draft overnight, then open the
dashboard and use **Fill form** (or **Run queue and fill forms**) when you are at the desk, review, and submit yourself.
