"""Contract checks for expiry acknowledgment, without Docker or OpenShell."""
import copy
import unittest
from control import applied_expiry


class ExpiryAcknowledgment(unittest.TestCase):
    def setUp(self):
        desired = dict(sandbox_id="original-uid", sandbox="original-name",
                       attachment_epoch="original-epoch", config_revision="2",
                       provider_env_revision="3", policy_hash="hash")
        self.receipt = {"targets": [{"state": "withheld", "reason": "credential_expired",
                                    "receipt": {"desired": desired}, "observed": {
                                        k: v for k, v in desired.items() if k not in ("sandbox_id", "sandbox")}}]}

    def test_withheld_requires_original_observed_revision(self):
        self.assertTrue(applied_expiry(self.receipt, self.receipt))
        for key in ("attachment_epoch", "provider_env_revision", "config_revision", "policy_hash"):
            pending = copy.deepcopy(self.receipt)
            pending["targets"][0]["observed"][key] = "old"
            self.assertFalse(applied_expiry(pending, self.receipt))
        self.assertFalse(applied_expiry({"targets": []}, self.receipt))

    def test_changed_target_or_desired_revision_never_adopted(self):
        for key in ("sandbox_id", "provider_env_revision", "attachment_epoch"):
            changed = copy.deepcopy(self.receipt)
            changed["targets"][0]["receipt"]["desired"][key] = "replacement"
            with self.assertRaises(RuntimeError):
                applied_expiry(changed, self.receipt)


if __name__ == "__main__":
    unittest.main()
