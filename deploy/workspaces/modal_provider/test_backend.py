"""Tests for failure paths where retrying against the wrong object could lose data."""
import unittest
from unittest.mock import Mock, patch

import modal
from errors import Failure
from modal_provider.backend import ModalBackend
from modal_provider.filesystem import file_operation


class ModalIdentityTests(unittest.TestCase):
    def setUp(self):
        self.backend = object.__new__(ModalBackend)
        self.backend.owner = "a" * 32
        self.backend.environment = "test"
        self.backend.app_name = "workspace-test"
        self.backend.volume = Mock()

    def test_volume_names_fit_and_are_stable_per_registration(self):
        data = "data-" + "b" * 32
        name = self.backend.volume_name(data)
        self.assertLess(len(name), 64)
        self.assertEqual(name, self.backend.volume_name(data))
        self.backend.owner = "c" * 32
        self.assertNotEqual(name, self.backend.volume_name(data))

    def test_missing_pinned_volume_is_not_recreated(self):
        self.backend.volume.return_value.hydrate.side_effect = modal.exception.NotFoundError("missing")
        with patch.object(modal.Volume.objects, "create") as create:
            with self.assertRaises(Failure) as caught:
                self.backend.claim("project", "vo-original")
            self.assertEqual(caught.exception.code, "workspace_data_missing")
            create.assert_not_called()

    def test_delete_cannot_remove_replacement_volume(self):
        self.backend.volume.return_value.object_id = "vo-replacement"
        with patch("modal_provider.backend.delete_volume_id") as delete:
            with self.assertRaises(Failure) as caught:
                self.backend.remove("data", "project", "vo-original")
            self.assertEqual(caught.exception.code, "workspace_identity_changed")
            delete.assert_not_called()

    def test_stop_cannot_terminate_replacement_sandbox(self):
        sb = Mock(object_id="sb-replacement")
        with patch.object(modal.Sandbox, "from_name", return_value=sb):
            with self.assertRaises(Failure):
                self.backend.remove("compute", "workspace", "sb-original")
            sb.terminate.assert_not_called()

    def test_failed_commit_does_not_repeat_mutation(self):
        sb = Mock()
        helper, commit = Mock(), Mock()
        helper.stdout.read.return_value = '{"result":{"edited":true}}'
        helper.wait.return_value = 0
        commit.wait.return_value = 1
        sb.exec.side_effect = [helper, commit]
        with self.assertRaisesRegex(RuntimeError, "commit"):
            file_operation(sb, "ws", {"name": "edit_file", "arguments": {}})
        self.assertEqual(sb.exec.call_count, 2)
        sb.reload_volumes.assert_called_once()
