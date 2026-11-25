"""Physical checks for the ignored kubernetes_workspace_recovery integration test."""
import argparse
import json
import pathlib
import re
import subprocess
import ssl
import urllib.request
import time


def until(fn, seconds=120):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.2)
    raise RuntimeError("Timed out waiting for workspace qualification")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--kubectl", default="kubectl")
    p.add_argument("--kubeconfig", required=True)
    p.add_argument("--registration", required=True, help="Dedicated test registration prefix")
    p.add_argument("--barrier", type=pathlib.Path, required=True)
    p.add_argument("--evidence", type=pathlib.Path, required=True)
    p.add_argument("--health-url", required=True)
    p.add_argument("--ca", required=True)
    args = p.parse_args()
    if not re.fullmatch(r"ow-m4-[a-z0-9-]+", args.registration):
        p.error("Expected a dedicated ow-m4-* test registration")
    pods = args.registration + "-pods"
    control = args.registration + "-control"

    def kube(ns, *cmd):
        return subprocess.check_output([args.kubectl, "--kubeconfig", args.kubeconfig, "-n", ns, *cmd], text=True)

    def get(ns, kind, name):
        return json.loads(kube(ns, "get", kind, name, "-o", "json"))

    def current_controller():
        items = json.loads(kube(control,"get","pods","-l","app=ow-workspaces","-o","json"))["items"]
        return next(o["metadata"]["uid"] for o in items if not o["metadata"].get("deletionTimestamp"))

    ids = args.barrier / "identities.json"
    until(ids.exists)
    refs = json.loads(ids.read_text())
    builder, reviewer = [refs[role]["workspaceId"].split(".", 1)[1] for role in ("builder", "reviewer")]
    data = refs["builder"]["dataId"].split(".",1)[1]
    assert builder != reviewer and refs["builder"]["dataId"] == refs["reviewer"]["dataId"]
    for name in (builder,reviewer):
        assert re.fullmatch(r"ws-[0-9a-f]{32}", name)
    claim = get(pods,"pvc",data)
    assert claim["status"]["phase"] == "Bound"
    owner = claim["metadata"]["labels"]["ow-workspace-owner"]

    def snapshot(pod):
        obj = get(pods, "pod", pod)
        assert obj["metadata"]["labels"]["ow-workspace-owner"] == owner
        assert [v["persistentVolumeClaim"]["claimName"] for v in obj["spec"]["volumes"] if "persistentVolumeClaim" in v] == [data]
        return {"name":pod, "uid":obj["metadata"]["uid"], "node":obj["spec"]["nodeName"]}

    before = [snapshot(w) for w in (builder,reviewer)]
    assert before[0]["uid"] != before[1]["uid"]
    for w in (builder,reviewer):
        assert kube(pods,"exec",w,"--","cat","/data/index.html") == "Hello Alice"
    old_controller = current_controller()
    state = get(control,"pvc","controller-state")["metadata"]["uid"]
    kube(control,"scale","deploy/controller","--replicas=0")
    kube(control,"wait","--for=delete","pod","-l","app=ow-workspaces","--timeout=90s")
    kube(pods,"delete","pod",builder,"--wait=true","--timeout=60s")
    kube(control,"scale","deploy/controller","--replicas=1")
    kube(control,"rollout","status","deploy/controller","--timeout=90s")
    new_controller = current_controller()
    assert new_controller != old_controller
    assert get(control,"pvc","controller-state")["metadata"]["uid"] == state
    # Deployment readiness precedes reconnection of an out-of-cluster
    # port-forward. Wait for the exact transport the Rust worker will use.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=args.ca)))
    def healthy():
        try:
            with opener.open(args.health_url,timeout=2) as response:
                return response.status == 200
        except OSError:
            return False
    until(healthy)
    (args.barrier/"continue").touch()
    until((args.barrier/"read-back").exists)
    after = [snapshot(w) for w in (builder,reviewer)]
    assert after[0]["uid"] != before[0]["uid"] and after[1]["uid"] == before[1]["uid"]
    assert get(pods,"pvc",data)["metadata"]["uid"] == claim["metadata"]["uid"]
    assert kube(pods,"exec",builder,"--","cat","/data/index.html") == "Hello Alice"
    args.evidence.write_text(json.dumps({"before":before,"after":after,"dataPVC":data,
        "dataPVCUID":claim["metadata"]["uid"],"pv":claim["spec"]["volumeName"],
        "metadataPVCUID":state,"controllerUIDs":[old_controller,new_controller],
        "fileAfterReplacement":"Hello Alice"},indent=2)+"\n")
    (args.barrier/"verified").touch()


if __name__ == "__main__":
    main()
