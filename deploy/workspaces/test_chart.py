"""Exercise the supported Helm profiles and incompatible storage placements."""
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("helm"), "Helm is required to render the workspace chart")
class WorkspaceChartTest(unittest.TestCase):
    def render(self, profile, *overrides):
        cmd = ["helm", "template", "websites", "chart", "-n", "websites-control",
               "-f", f"examples/{profile}.yaml", "--set",
               "images.controller=test/controller:v1,images.runner=test/runner:v1"]
        for value in overrides:
            cmd.extend(["--set", value])
        return subprocess.run(cmd, cwd=Path(__file__).parent, text=True, capture_output=True)

    def test_supported_profiles(self):
        for profile, storage, spread in [
            ("local", "standard", "false"),
            ("eks", "workspace-efs", "true"),
        ]:
            with self.subTest(profile=profile):
                result = self.render(profile)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f'{{name: STORAGE_CLASS, value: "{storage}"}}', result.stdout)
                self.assertIn(f'{{name: SPREAD_SHARED_DATA, value: "{spread}"}}', result.stdout)
                self.assertIn("helm.sh/resource-policy: keep", result.stdout)
                self.assertNotIn("kind: Namespace", result.stdout)

    def test_reinstall_with_existing_database(self):
        result = self.render("eks", "metadata.existingClaim=retained-database")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("kind: PersistentVolumeClaim", result.stdout)
        self.assertIn('claimName: "retained-database"', result.stdout)

    def test_invalid_combinations(self):
        for profile, value in [
            ("local", "placement.pinNode="),
            ("eks", "placement.pinNode=worker"),
            ("eks", "data.singleMount=true"),
            ("eks", "workloadNamespace=websites-control"),
            ("eks", "idleSeconds=-1"),
        ]:
            with self.subTest(value=value):
                self.assertNotEqual(self.render(profile, value).returncode, 0)
