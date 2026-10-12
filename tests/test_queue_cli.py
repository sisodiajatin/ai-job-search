"""queue_cli.py (for a scheduler such as Hermes Agent) and the whole-queue lock that keeps runners from colliding.

The runner itself is faked: these tests are about the command's contract (output, exit codes, flags) and the lock.
"""

import contextlib
import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "web"))
import queue_cli
from api import queue_runner
from api.launcher import LaunchError
from api.run_lock import application_lock

RESULTS = [
    {"company": "Acme", "title": "Engineer", "status": "drafted", "score": 82, "cv": "cv/x.pdf", "cover": "cover_letters/x.pdf",
     "warnings": ["w"], "form": {"status": "filled", "reason": "ok"}},
    {"company": "Beta", "title": "Dev", "status": "skipped", "score": 41, "reason": "below the floor"},
    {"company": "Gamma", "title": "SWE", "status": "failed", "reason": "timed out"},
]


def run_cli(argv, statuses=None, start_error=None):
    """Run queue_cli.main with a fake runner; returns (exit code, printed output, the fake start mock)."""
    out = io.StringIO()
    statuses = list(statuses or [])
    with patch.object(queue_runner, "start", side_effect=start_error) as start, \
         patch.object(queue_runner, "status", side_effect=statuses + [statuses[-1]] * 3 if statuses else None), \
         patch.object(queue_runner, "stop") as stop, \
         patch.object(queue_cli.time, "sleep"), \
         contextlib.redirect_stdout(out):
        code = queue_cli.main(argv)
    return code, out.getvalue(), start, stop


class QueueCliTests(unittest.TestCase):
    def finished(self, **extra):
        return {"running": False, "events": [], "last_id": 5, "results": RESULTS, "message": "Queue finished.", **extra}

    def test_status_lists_the_queue_and_runs_nothing(self):
        queue = [{"url": "https://e.com/a", "company": "Acme", "title": "Engineer"}]
        out = io.StringIO()
        with patch("api.store.list_queue", return_value=queue), patch.object(queue_runner, "start") as start, contextlib.redirect_stdout(out):
            self.assertEqual(queue_cli.main(["--status"]), 0)
        start.assert_not_called()
        self.assertIn("1 job(s) in the queue", out.getvalue())
        self.assertIn("Acme | Engineer", out.getvalue())

    def test_streams_progress_then_prints_a_summary(self):
        running = {"running": True, "events": [{"id": 1, "ts": "10:00:00", "message": "Starting Acme"}], "last_id": 1, "results": [], "message": ""}
        code, text, start, _ = run_cli([], [running, self.finished()])
        self.assertEqual(code, 0)
        start.assert_called_once_with(fill_forms=False)
        self.assertIn("[10:00:00] Starting Acme", text)
        self.assertIn("Done: 1 drafted, 1 skipped, 1 failed.", text)

    def test_fill_forms_flag_is_passed_and_counted(self):
        code, text, start, _ = run_cli(["--fill-forms"], [self.finished()])
        start.assert_called_once_with(fill_forms=True)
        self.assertIn("1 form(s) filled", text)

    def test_json_summary_is_the_last_line_and_carries_no_documents(self):
        _, text, _, _ = run_cli(["--json"], [self.finished()])
        summary = json.loads(text.strip().splitlines()[-1])
        self.assertEqual((summary["drafted"], summary["skipped"], summary["failed"]), (1, 1, 1))
        self.assertEqual(summary["jobs"][0], {"company": "Acme", "title": "Engineer", "status": "drafted", "score": 82, "reason": "", "form": "filled"})
        self.assertNotIn("x.pdf", text.splitlines()[-1])  # paths to the CV and letter stay out of what a scheduler relays

    def test_cannot_start_exit_codes(self):
        code, text, _, _ = run_cli([], start_error=LaunchError("The queue is empty. Queue some jobs first.", 400))
        self.assertEqual(code, 1)
        self.assertIn("The queue is empty", text)
        self.assertEqual(run_cli([], start_error=LaunchError("The queue is already running.", 409))[0], 3)

    def test_a_run_that_found_the_queue_busy_exits_3(self):
        busy = {"running": False, "events": [], "last_id": 1, "results": [], "message": "The queue is already running (in the dashboard or another command), so this run did nothing."}
        self.assertEqual(run_cli([], [busy])[0], 3)

    def test_ctrl_c_stops_the_current_job(self):
        running = {"running": True, "events": [], "last_id": 0, "results": [], "message": ""}
        done = {**running, "running": False}
        out = io.StringIO()
        with patch.object(queue_runner, "start"), patch.object(queue_runner, "status", side_effect=[running, running, done]), \
             patch.object(queue_runner, "stop") as stop, patch.object(queue_cli.time, "sleep", side_effect=[KeyboardInterrupt, None]), \
             contextlib.redirect_stdout(out):
            self.assertEqual(queue_cli.main([]), 130)
        stop.assert_called_once()


class QueueLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        for patcher in (patch.object(queue_runner, "RESULTS_DIR", self.dir),):
            patcher.start()
            self.addCleanup(patcher.stop)
        queue_runner._state.update(running=True, stop_requested=False, events=[], next_id=1, current=None, message="", results=[])

    def test_a_second_runner_does_nothing_while_the_queue_lock_is_held(self):
        held, release = threading.Event(), threading.Event()

        def other_runner():
            with application_lock(queue_runner.QUEUE_LOCK_KEY, self.dir):  # another process, as far as the lock can tell
                held.set()
                release.wait(5)

        thread = threading.Thread(target=other_runner)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(release.set)
        self.assertTrue(held.wait(5))
        with patch.object(queue_runner, "_run_queue") as work:
            queue_runner._run()
        work.assert_not_called()
        self.assertFalse(queue_runner._state["running"])
        self.assertIn("already running", queue_runner._state["message"])

    def test_the_lock_is_released_so_the_next_run_works(self):
        with patch.object(queue_runner, "_run_queue") as work:
            queue_runner._run()
            queue_runner._run()
        self.assertEqual(work.call_count, 2)


if __name__ == "__main__":
    unittest.main()
