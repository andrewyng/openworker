"""Controller request validation, run against an in-memory backend."""
import tempfile
import unittest

from errors import Failure
from service import Controller


class FakeBackend:
    identity = "kubernetes"
    single_mount = False

    def __init__(self, installation):
        self.installation = installation

    def claim(self, data, uid):
        return uid or "pvc-uid"

    def has_compute(self, workspace, uid):
        return True

    def compute(self, workspace, data, profile, previous, data_uid, **runtime):
        return previous or "pod-uid"

    def ready(self, workspace, physical):
        return True

    def remove(self, kind, name, uid):
        return True


class DetachTest(unittest.TestCase):
    def setUp(self):
        state = tempfile.TemporaryDirectory()
        self.addCleanup(state.cleanup)
        self.controller = Controller(FakeBackend, state.name)
        self.addCleanup(self.controller.lock_file.close)
        self.addCleanup(self.controller.db.close)
        opened = self.controller.call("open", {"key": "project", "defaultProfile": "small"})
        self.workspace = opened["workspaceId"]

    def attachments(self):
        return [tuple(row) for row in self.controller.db.execute(
            "SELECT session, active FROM attachments WHERE workspace=?", (self.workspace,))]

    def detach(self, session):
        return self.controller.call("detach", {"workspaceId": self.workspace, "sessionId": session})

    def test_detach_rejects_invalid_session_ids_without_writing(self):
        for session in ["", "a" * 129, "bad id", "../x", "sess\n1"]:
            with self.subTest(session=session):
                with self.assertRaises(Failure) as caught:
                    self.detach(session)
                self.assertEqual(
                    (caught.exception.code, caught.exception.status),
                    ("invalid_workspace_request", 400),
                )
        self.assertEqual(self.attachments(), [])

    def test_detach_marks_an_attached_session_inactive(self):
        self.controller.call("attach", {"workspaceId": self.workspace, "sessionId": "sess-1"})
        self.assertEqual(self.attachments(), [("sess-1", 1)])
        self.assertEqual(self.detach("sess-1"), {})
        self.assertEqual(self.attachments(), [("sess-1", 0)])

    def test_attach_still_rejects_invalid_session_ids(self):
        with self.assertRaises(Failure) as caught:
            self.controller.call("attach", {"workspaceId": self.workspace, "sessionId": "a" * 129})
        self.assertEqual(caught.exception.status, 400)
        self.assertEqual(self.attachments(), [])


if __name__ == "__main__":
    unittest.main()
