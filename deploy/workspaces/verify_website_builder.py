"""Read-only cluster checks for the website_builder_app integration test."""
import argparse
import json
import pathlib
import subprocess
import time


def until(check, description):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(.25)
    raise TimeoutError(description)


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument("--kubectl", default="kubectl")
    p.add_argument("--kubeconfig", required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("--eks-controller-namespace", help="Require distinct worker nodes, EBS controller state and EFS project data")
    p.add_argument("--barrier", type=pathlib.Path, required=True)
    p.add_argument("--evidence", type=pathlib.Path, required=True)
    args = p.parse_args()
    kube = [args.kubectl, "--kubeconfig", args.kubeconfig, "-n", args.namespace]

    def items(kind):
        return json.loads(subprocess.check_output(kube + ["get", kind, "-o", "json"]))["items"]

    def pods():
        return {pod["metadata"]["uid"]: {
            "pod": pod["metadata"]["name"],
            "claim": next(v["persistentVolumeClaim"]["claimName"] for v in pod["spec"]["volumes"]
                          if "persistentVolumeClaim" in v),
            "node": pod["spec"]["nodeName"],
        } for pod in items("pods")}

    def claims():
        return {p["metadata"]["name"]: (p["metadata"]["uid"], p["spec"]["volumeName"])
                for p in items("pvc") if p["status"]["phase"] == "Bound"}

    def expect_file(pod, heading):
        page = subprocess.check_output(kube + ["exec", pod, "--", "cat", "/data/index.html"], text=True)
        assert "<h1>" + heading + "</h1>" in page

    def bound_driver(pv):
        raw = subprocess.check_output(kube + ["get", "pv", pv, "-o", "json"])
        return json.loads(raw)["spec"]["csi"]["driver"]

    receipts = {}
    for step in ("built", "shared", "separate", "bob", "app-stopped", "edited"):
        until((args.barrier / step).exists, step)
        if step == "app-stopped":
            until(lambda: not items("pods"), "idle compute reclamation")
        current, volumes = pods(), claims()
        if step == "built":
            assert len(current) == len(volumes) == 1
            original = current
            alice = next(iter(volumes))
        elif step == "shared":
            assert current == original
        elif step == "separate":
            assert len(current) == 2 and all(current.get(k) == v for k, v in original.items())
            assert {p["claim"] for p in current.values()} == {alice}
        elif step == "bob":
            assert len(volumes) == 2
            bob, = volumes.keys() - {alice}
            saved = volumes
            assert any(p["claim"] == bob for p in current.values())
        elif step == "app-stopped":
            assert not current and volumes == saved
        elif step == "edited":
            assert volumes == saved and {p["claim"] for p in current.values()} == {alice, bob}
            assert not original.keys() & current.keys()
            assert not {p["pod"] for p in original.values()} & {p["pod"] for p in current.values()}
        if args.eks_controller_namespace:
            if step == "separate":
                assert len({p["node"] for p in current.values()}) == 2, "Expected different Kubernetes nodes"
            if step == "built":
                raw = subprocess.check_output([args.kubectl, "--kubeconfig", args.kubeconfig,
                    "-n", args.eks_controller_namespace, "get", "pvc", "controller-state", "-o", "json"])
                metadata_pv = json.loads(raw)["spec"]["volumeName"]
                assert bound_driver(metadata_pv) == "ebs.csi.aws.com"
                receipts["metadata"] = {"pv": metadata_pv, "driver": "ebs.csi.aws.com"}
            for _, pv in volumes.values():
                assert bound_driver(pv) == "efs.csi.aws.com"
        for entry in current.values():
            heading = "Bob’s studio" if entry["claim"] != alice else (
                "Alice’s autumn garden" if step == "edited" else "Alice’s garden")
            expect_file(entry["pod"], heading)
        receipts[step] = {"pods": current, "boundVolumes": volumes}
        args.evidence.write_text(json.dumps(receipts, indent=2) + "\n")
        (args.barrier / (step + ".ok")).touch()
        print(step, flush=True)


if __name__ == "__main__":
    main()
