"""Reconcile must not poll-drain under the controller lock (#928).

A stuck removal previously held the lock for up to 6s per row inside every
3s reconcile cycle, stalling all API calls (`call()` takes the same lock).
"""
import contextlib
import io
import tempfile
import time
import unittest
from unittest.mock import Mock

from service import Controller


class ReconcileTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.backend = Mock(identity="kubernetes", single_mount=False, images={})
        self.svc = Controller(lambda _: self.backend, tmp.name)
        self.addCleanup(self.svc.lock_file.close)
        self.addCleanup(self.svc.db.close)

    def seed(self, workspace="ws-1", stopping=1, idle=None):
        idle = time.time() if idle is None else idle
        with self.svc.db:
            self.svc.db.execute("INSERT OR IGNORE INTO data(id) VALUES(?)", ("data-1",))
            self.svc.db.execute("INSERT OR IGNORE INTO keys(key,data) VALUES(?,?)", ("k1", "data-1"))
            self.svc.db.execute(
                "INSERT OR REPLACE INTO compute(id,key,profile,uid,stopping,idle) VALUES(?,?,?,?,?,?)",
                (workspace, "k1", "small", "uid-1", stopping, idle),
            )

    def test_busy_removal_is_single_shot_and_leaves_intent(self):
        # A backend that never confirms removal: the cycle must try once and
        # return, not sleep-poll for 6s while holding the lock (#928).
        self.seed()
        self.backend.remove.return_value = False
        start = time.monotonic()
        self.svc.reconcile_once()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.0, f"cycle held for {elapsed:.1f}s")
        self.assertEqual(self.backend.remove.call_count, 1)
        row = self.svc.row("SELECT stopping FROM compute WHERE id='ws-1'")
        self.assertIsNotNone(row, "intent row must survive a busy removal")
        self.assertEqual(row["stopping"], 1)

    def test_idle_expired_row_is_driven_once_per_cycle(self):
        # The idle branch of the pending query: stopping=0 row picked up,
        # one remove attempt, intent flag set for the next cycle.
        self.seed(stopping=0, idle=time.time() - 10_000)
        self.backend.remove.return_value = False
        start = time.monotonic()
        self.svc.reconcile_once()
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.0, f"cycle held for {elapsed:.1f}s")
        self.assertEqual(self.backend.remove.call_count, 1)
        row = self.svc.row("SELECT stopping FROM compute WHERE id='ws-1'")
        self.assertEqual(row["stopping"], 1)

    def test_confirmed_removal_clears_the_row(self):
        self.seed()
        self.backend.remove.return_value = True
        self.svc.reconcile_once()
        self.assertEqual(self.backend.remove.call_count, 1)
        self.assertIsNone(self.svc.row("SELECT id FROM compute WHERE id='ws-1'"))

    def test_user_initiated_stop_still_polls_to_completion(self):
        # call("stop") keeps its bounded polling: one failed check followed by
        # a confirmed removal must finish the stop within the same call.
        self.seed(stopping=0)
        self.backend.remove.side_effect = [False, True]
        self.svc.stop("ws-1")
        self.assertEqual(self.backend.remove.call_count, 2)
        self.assertIsNone(self.svc.row("SELECT id FROM compute WHERE id='ws-1'"))

    def test_healthy_cleanup_does_not_log_a_retry(self):
        # The delete was issued and the object is still terminating: the normal
        # path of a healthy cleanup. The row is finished by the next cycle, so
        # it must not show up in the log as a retry of a stuck row.
        self.seed()
        self.backend.remove.return_value = False
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.svc.reconcile_once()
        self.assertNotIn("cleanup retry", out.getvalue())
        # ...and the row survives, with its intent flag, for the next cycle.
        row = self.svc.row("SELECT stopping FROM compute WHERE id='ws-1'")
        self.assertIsNotNone(row)
        self.assertEqual(row["stopping"], 1)

    def test_a_real_failure_is_logged_as_a_retry(self):
        # A backend that errors is the case the log line is for.
        self.seed()
        self.backend.remove.side_effect = RuntimeError("api down")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.svc.reconcile_once()
        self.assertIn("cleanup retry ws-1 RuntimeError", out.getvalue())

    def test_the_next_cycle_finishes_a_healthy_cleanup(self):
        # The first cycle issues the delete (the object is still terminating),
        # the second one finds it gone and clears the row.
        self.seed()
        self.backend.remove.side_effect = [False, True]
        self.svc.reconcile_once()
        self.assertIsNotNone(self.svc.row("SELECT id FROM compute WHERE id='ws-1'"))
        self.svc.reconcile_once()
        self.assertIsNone(self.svc.row("SELECT id FROM compute WHERE id='ws-1'"))
