"""Drive native probe expiry/refresh/detach barriers on a LOCAL fixture gateway.

No token is read here. Updates only the synthetic fixture provider's validity and
attachment. One mutation per stage; errors retain original resources for inspection.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.parse import urlsplit


def applied_expiry(receipt, original):
    """Withheld is a control-plane decision, not a supervisor acknowledgment.

    An immediate expiry update may report `withheld` with an OLD observed
    revision. Do not release the Git test until this exact original target has
    acknowledged the desired revision. Observe only; never retry the mutation.
    """
    targets = receipt.get("targets", [])
    expected = original["targets"]
    if len(targets) != len(expected):
        return False
    for current, prior in zip(targets, expected):
        desired = prior["receipt"]["desired"]
        if current["receipt"]["desired"] != desired:
            raise RuntimeError("Original provider change was replaced; inspect without replay")
        observed = current.get("observed", {})
        if not (current["state"] == "withheld" and current["reason"] == "credential_expired"
                and observed.get("provider_env_revision") == desired["provider_env_revision"]
                and observed.get("attachment_epoch") == desired["attachment_epoch"]
                and observed.get("config_revision") == desired["config_revision"]
                and observed.get("policy_hash") == desired["policy_hash"]):
            return False
    return bool(targets)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cli", required=True)
    parser.add_argument("--xdg-config", required=True)
    parser.add_argument("--probe", required=True)
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    state = Path(config["state"])
    endpoint = urlsplit(config["endpoint"])
    assert endpoint.scheme == "https" and endpoint.hostname in ("localhost", "127.0.0.1")
    assert config["provider"].startswith("owgit-c6-") and config["host"].endswith(".svc.cluster.local:8443")
    assert config["barrier"] and not (state / "intent.json").exists(), "Fresh explicit qualification attempt required"
    env = dict(os.environ, XDG_CONFIG_HOME=args.xdg_config)
    cli = [args.cli, "--gateway-endpoint", config["endpoint"], "--workspace", "default"]
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Native probe emits only pass markers/generic failures; Git output remains
    # in its private failure receipt instead of being printed to a shared log.
    with (state / "native.log").open("w") as log:
        child = subprocess.Popen([args.probe, args.config], stdout=log, stderr=log)
        try:
            def ready(stage):
                until = time.monotonic() + 150
                while not (state / (stage + ".ready")).exists():
                    if child.poll() is not None:
                        raise RuntimeError("Native probe stopped; inspect original private receipt")
                    if time.monotonic() > until:
                        raise RuntimeError("Fixture barrier timed out; inspect original resource")
                    time.sleep(0.1)

            def mutation(stage, command, expired=False):
                p = subprocess.run(cli + command, env=env, capture_output=True, text=True, timeout=40)
                (state / (stage + "-receipt.json")).write_text(p.stdout)
                (state / (stage + "-diagnostic.txt")).write_text(p.stderr)
                receipt = json.loads(p.stdout)
                if expired:
                    # The update PERSISTED. Nonzero CLI status reports terminal
                    # credential withholding, not permission to repeat mutation.
                    assert receipt.get("mutation_id") and receipt.get("targets")
                    assert all(t["state"] == "withheld" and t["reason"] == "credential_expired" and t["wait_outcome"] == "terminal" for t in receipt["targets"])
                    binding = json.loads((state / "binding.json").read_text())
                    assert len(receipt["targets"]) == 1
                    target = receipt["targets"][0]["receipt"]
                    assert target["desired"]["sandbox_id"] == binding["remote_id"]
                    until = time.monotonic() + 40
                    observed = receipt
                    while not applied_expiry(observed, receipt):
                        if time.monotonic() > until:
                            raise RuntimeError("Original expiry not acknowledged by supervisor; no Git operation/replay")
                        time.sleep(0.5)
                        status = subprocess.run(cli + ["sandbox", "provider", "status", binding["remote_name"], config["provider"], "--receipt", target["receipt_id"], "--output", "json"], env=env, capture_output=True, text=True, timeout=15)
                        observed = json.loads(status.stdout)
                        (state / "expired-applied.json").write_text(status.stdout)
                elif p.returncode:
                    raise RuntimeError("Mutation outcome needs inspection; never automatically retry")
                (state / (stage + ".continue")).write_text("original receipt inspected")
                print("PASS fixture control " + stage, flush=True)

            ready("expired")
            mutation("expired", ["provider", "update", config["provider"], "--credential-expires-at", "GITHUB_TOKEN=1", "--wait", "--timeout", "30", "--output", "json"], expired=True)
            ready("refreshed")
            mutation("refreshed", ["provider", "update", config["provider"], "--credential-expires-at", "GITHUB_TOKEN=0", "--wait", "--timeout", "30", "--output", "json"])
            ready("revoked")
            binding = json.loads((state / "binding.json").read_text())
            p = subprocess.run(cli + ["sandbox", "get", binding["remote_name"], "--output", "json"], env=env, capture_output=True, text=True, check=True, timeout=20)
            live = json.loads(p.stdout)
            assert live["id"] == binding["remote_id"] and live["labels"]["openworker-environment"] == binding["environment_id"]
            mutation("revoked", ["sandbox", "provider", "detach", binding["remote_name"], config["provider"], "--wait", "--timeout", "30", "--output", "json"])
            assert child.wait(timeout=120) == 0, "Native Git/cleanup checks failed; inspect private receipt"
            result = json.loads((state / "result.json").read_text())
            assert result["passed"] and result["credential_detach_revocation"] and result["cleanup_confirmed"]
            print("PASS live Git expiry, refreshed process-manager reference, detach and original cleanup")
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
                print("Native probe stopped. Retain and inspect its ORIGINAL fixture receipt; no automatic delete/retry.")


if __name__ == "__main__":
    main()
