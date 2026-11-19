"""Exercise partially provisioned compute and interrupted deletion in a test namespace."""
import json
import os
import pathlib
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import uuid


def run():
    # This destructive fixture runs only in namespaces dedicated to this qualification.
    prefix = os.environ["M4_REGISTRATION"]
    if not prefix.startswith("ow-m4-"):
        raise ValueError("Dedicated test registration required")
    workload, control = prefix + "-pods", prefix + "-control"
    base = [os.environ["M4_KUBECTL"], "--kubeconfig", os.environ["M4_KUBECONFIG"]]
    def kube(ns, *cmd, body=None):
        return subprocess.check_output(base + ["-n",ns,*cmd], text=True, input=body)

    def items(kind):
        return json.loads(kube(workload,"get",kind,"-o","json"))["items"]
    url = os.environ["M4_ENDPOINT"]
    token = pathlib.Path(os.environ["M4_TOKEN_FILE"]).read_text().strip()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=os.environ["M4_CA"])))
    def rpc(action, **body):
        req = urllib.request.Request(url + "/v1/" + action,
            data=json.dumps(body).encode(), headers={"Authorization":"Bearer " + token, "Content-Type":"application/json"})
        try:
            with opener.open(req, timeout=25) as response:
                return response.status,json.load(response)
        except urllib.error.HTTPError as err:
            return err.code,json.load(err)
    def ready():
        kube(control,"rollout","status","deploy/controller","--timeout=90s")
        for _ in range(90):
            try:
                if rpc("start",options={})[0] == 200:
                    return
            except OSError:
                pass
            time.sleep(.5)
        raise RuntimeError("Controller unavailable")
    if items("pods") or items("pvc"):
        raise RuntimeError("Expected a clean workload namespace")
    key = uuid.uuid4().hex
    quota = {"apiVersion":"v1","kind":"ResourceQuota","metadata":{"name":"m4-interruption"},
             "spec":{"hard":{"pods":"0"}}}
    kube(workload,"create","-f","-",body=json.dumps(quota))
    try:
        # Wait until quota admission has observed this constraint.
        kube(workload,"wait","resourcequota/m4-interruption","--for=jsonpath={.status.hard.pods}=0","--timeout=40s")
        assert rpc("open",key=key,defaultProfile="small")[0] == 503
        claims=items("pvc")
        assert len(claims)==1 and not items("pods")
        claim=claims[0]["metadata"]
        kube(control,"rollout","restart","deploy/controller")
        ready()
    finally:
        kube(workload,"delete","resourcequota","m4-interruption")
    status,ref=rpc("open",key=key,defaultProfile="small")
    assert status==200 and ref["dataId"]==claim["name"] and len(items("pods"))==1
    assert items("pvc")[0]["metadata"]["uid"]==claim["uid"]
    assert rpc("open",key=key,defaultProfile="small")== (200,ref)
    kube(workload,"wait","--for=condition=Ready","pod/"+ref["workspaceId"],"--timeout=90s")
    assert rpc("stop",workspaceId=ref["workspaceId"])[0]==200
    # A test-owned finalizer holds the actual claim across a controller restart.
    pvc = json.loads(kube(workload,"get","pvc",ref["dataId"],"-o","json"))
    pv = pvc["spec"]["volumeName"]
    marker="qualification.openworker.dev/hold"
    finalizers=pvc["metadata"].get("finalizers",[])
    kube(workload,"patch","pvc",ref["dataId"],"--type=json","-p",json.dumps([
        {"op":"test","path":"/metadata/resourceVersion","value":pvc["metadata"]["resourceVersion"]},
        {"op":"add","path":"/metadata/finalizers","value":finalizers+[marker]}]))
    assert rpc("delete",dataId=ref["dataId"])[0]==409
    kube(control,"rollout","restart","deploy/controller")
    ready()
    pending = json.loads(kube(workload,"get","pvc",ref["dataId"],"-o","json"))["metadata"]
    assert pending["uid"]==claim["uid"] and pending["deletionTimestamp"]
    index=pending["finalizers"].index(marker)
    kube(workload,"patch","pvc",ref["dataId"],"--type=json","-p",json.dumps([
        {"op":"test","path":"/metadata/uid","value":claim["uid"]},
        {"op":"test","path":f"/metadata/finalizers/{index}","value":marker},
        {"op":"remove","path":f"/metadata/finalizers/{index}"}]))
    kube(workload,"wait","--for=delete","pvc/"+ref["dataId"],"--timeout=60s")
    kube(workload,"wait","--for=delete","pv/"+pv,"--timeout=60s")
    for _ in range(40):
        if rpc("open",key="stale-"+key,dataId=ref["dataId"],defaultProfile="small")[0]==404:
            break
        time.sleep(.5)
    else:
        raise RuntimeError("Unfinished data deletion")
    assert not items("pods") and not items("pvc")
    # Shorten reclamation only on this dedicated test deployment and restore it.
    deploy = json.loads(kube(control,"get","deploy/controller","-o","json"))
    env = deploy["spec"]["template"]["spec"]["containers"][0]["env"]
    old_idle = next(entry["value"] for entry in env if entry["name"]=="IDLE_SECONDS")
    try:
        kube(control,"set","env","deploy/controller","IDLE_SECONDS=5")
        ready()
        status,first = rpc("open",key=key,defaultProfile="small")
        assert status==200 and first["dataId"]!=ref["dataId"]
        session = {"workspaceId":first["workspaceId"],"sessionId":"idle-test"}
        assert rpc("attach",**session)[0]==200
        assert rpc("file",**session,operation={"name":"write_file","arguments":{"path":"state","content":"saved"}})[0]==200
        time.sleep(9)
        assert rpc("file",**session,operation={"name":"read_file","arguments":{"path":"state"}})[1]["text"]=="saved"
        assert rpc("detach",**session)[0]==200
        kube(workload,"wait","--for=delete","pod/"+first["workspaceId"],"--timeout=35s")
        # The physical delete can precede the final SQLite cleanup transaction.
        for _ in range(20):
            status,later=rpc("open",key=key,defaultProfile="small")
            if status==200:
                break
            time.sleep(.2)
        assert status==200 and later["dataId"]==first["dataId"] and later["workspaceId"]!=first["workspaceId"]
        recovered={"workspaceId":later["workspaceId"],"sessionId":"idle-recovered"}
        assert rpc("attach",**recovered)[0]==200
        assert rpc("file",**recovered,operation={"name":"read_file","arguments":{"path":"state"}})[1]["text"]=="saved"
        assert rpc("detach",**recovered)[0]==200
        assert rpc("stop",workspaceId=later["workspaceId"])[0]==200
        assert rpc("delete",dataId=later["dataId"])[0]==200
    finally:
        kube(control,"set","env","deploy/controller","IDLE_SECONDS="+old_idle)
        ready()
    print(json.dumps({"result":"passed","preservedClaimUID":claim["uid"],"reclaimedPV":pv,
        "cases":["PVC created before pod admission failure","restart and retry reused the PVC",
                 "interrupted PVC deletion across restart","backing PV reclaimed","active session survives idle threshold","idle pod reclaimed; fresh compute reopened saved data"]},indent=2))


if __name__=="__main__":
    run()
