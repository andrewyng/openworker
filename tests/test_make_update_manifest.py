import json
import subprocess
import sys
from pathlib import Path


def test_signed_linux_artifact_produces_linux_x86_64_entry(tmp_path: Path) -> None:
    asset: str = "OpenWorker-linux-x86_64.AppImage.tar.gz"
    (tmp_path / asset).write_bytes(b"appimage updater archive")
    (tmp_path / f"{asset}.sig").write_text("linux-signature\n")
    output = tmp_path / "latest.json"
    script = Path(__file__).parents[1] / "packaging" / "make_update_manifest.py"

    subprocess.run(
        [
            sys.executable,
            str(script),
            "--version",
            "0.1.6",
            "--tag",
            "v0.1.6",
            "--repo",
            "andrewyng/openworker",
            "--dist",
            str(tmp_path),
            "--out",
            str(output),
        ],
        check=True,
    )

    manifest = json.loads(output.read_text())
    assert manifest["platforms"]["linux-x86_64"] == {
        "signature": "linux-signature",
        "url": (
            "https://github.com/andrewyng/openworker/releases/download/v0.1.6/"
            "OpenWorker-linux-x86_64.AppImage.tar.gz"
        ),
    }
