import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from errors import Failure
from service import Controller


class PreviewTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.backend = Mock(identity="kubernetes", single_mount=False, images={"nodejs": "image@sha256:123"})
        self.backend.claim.return_value = "claim-uid"
        self.backend.compute.return_value = "first-pod"
        self.backend.has_compute.return_value = True
        with patch.dict("os.environ", {"PREVIEW_DOMAIN": "preview.test"}):
            self.svc = Controller(lambda _: self.backend, tmp.name)
        self.addCleanup(self.svc.lock_file.close)
        self.addCleanup(self.svc.db.close)

    def open(self, **opts):
        return self.svc.call("open", {"key": "project", "defaultProfile": "small", "options": opts})

    def test_validation_precedes_provisioning(self):
        for options in [{"previewPortCount": n} for n in (-1, 16, True, 1.5, "5")] + [{"imageProfile": "bad"}]:
            with self.subTest(options=options), self.assertRaises(Failure):
                self.open(**options)
        self.backend.compute.assert_not_called()

    def test_slot_scope_and_replacement(self):
        a = self.open(previewPortCount=5)
        one, two = a["previews"][:2]
        host = one["url"].removeprefix("https://")
        self.backend.resource.return_value = SimpleNamespace(
            metadata=SimpleNamespace(uid="first-pod", labels={"ow-workspace-preview": "enabled"}, deletion_timestamp=None),
            spec=SimpleNamespace(host_network=False), status=SimpleNamespace(pod_ip="10.0.1.2"))
        self.assertEqual(self.svc.resolve(host, one["accessToken"])["port"], 5100)
        with self.assertRaises(Failure):
            self.svc.resolve(host, two["accessToken"])
        with self.assertRaises(Failure):
            self.svc.resolve(host.replace("-00.", "-05."), one["accessToken"])
        self.backend.compute.return_value = "replacement"
        self.backend.has_compute.return_value = False
        b = self.open()
        self.assertEqual(len(b["previews"]), 5)
        self.assertNotEqual(one["url"], b["previews"][0]["url"])
        with self.assertRaises(Failure) as error:
            self.svc.resolve(host, one["accessToken"])
        self.assertEqual(error.exception.status, 410)

    def test_file_only_default_and_unsupported_process(self):
        ref = self.open()
        self.assertNotIn("previews", ref)
        ctx = {"workspaceId": ref["workspaceId"], "sessionId": "s"}
        self.svc.call("attach", ctx)
        with self.assertRaises(Failure):
            self.svc.call("process", {**ctx,"operation":{"name":"process_start","arguments":{"command":"vite"}}})
        self.backend.process.assert_not_called()
