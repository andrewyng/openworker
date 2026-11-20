"""Reconcile must not poll-drain under the controller lock (#928).

A stuck removal previously held the lock for up to 6s per row inside every
3s reconcile cycle, stalling all API calls (`call()` takes the same lock).
"""
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
