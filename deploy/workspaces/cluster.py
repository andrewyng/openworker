"""Kubernetes operations in one dedicated workload namespace."""
import json
import os
import time

from kubernetes import client, config, stream
from kubernetes.client.exceptions import ApiException

from errors import Failure


class Cluster:
    identity = "kubernetes"

    def __init__(self, owner):
        config.load_incluster_config()
        self.api = client.CoreV1Api()
        self.namespace = os.environ["WORKLOAD_NAMESPACE"]
        self.owner = owner
        self.image = os.environ["RUNNER_IMAGE"]
        self.mode = os.getenv("DATA_MODE", "ReadWriteMany")
        self.single_mount = self.mode == "ReadWriteOncePod" or os.getenv("ALLOW_SHARED_COMPUTE") == "false"
        self.node = os.getenv("PIN_NODE")
        self.spread = os.getenv("SPREAD_SHARED_DATA", "false") == "true"
        self.storage_class = os.environ["STORAGE_CLASS"]
        if self.mode not in ("ReadWriteMany", "ReadWriteOnce", "ReadWriteOncePod"):
            raise ValueError("Unsupported DATA_MODE")
        if self.mode == "ReadWriteOnce" and not self.node:
            raise ValueError("ReadWriteOnce sharing requires PIN_NODE")

    def metadata(self, name):
        return {"name": name, "labels": {"ow-workspace-owner": self.owner}}

    def has_compute(self, name, uid):
        return self.resource("pod", name, uid) is not None

    def compute(self, workspace, data, profile, previous_uid, data_uid):
        return self.pod(workspace, data, profile, previous_uid).metadata.uid

    def ready(self, workspace, uid):
        pod = self.resource("pod", workspace, uid)
        return pod is not None and any(c.type == "Ready" and c.status == "True" for c in pod.status.conditions or [])

    def resource(self, kind, name, pinned=None):
        read = self.api.read_namespaced_pod if kind == "pod" else self.api.read_namespaced_persistent_volume_claim
        try:
            obj = read(name, self.namespace, _request_timeout=5)
        except ApiException as e:
            if e.status == 404:
                return None
            raise
        if ((obj.metadata.labels or {}).get("ow-workspace-owner") != self.owner
                or (pinned and obj.metadata.uid != pinned)):
            raise Failure("workspace_identity_changed", 409)
        return obj

    def ensure(self, kind, body, pinned=None):
        name = body["metadata"]["name"]
        obj = self.resource(kind, name, pinned)
        if obj is None:
            if pinned:
                raise Failure("workspace_data_missing", 409)
            create = self.api.create_namespaced_pod if kind == "pod" else self.api.create_namespaced_persistent_volume_claim
            try:
                create(self.namespace, body, _request_timeout=8)
            except ApiException as e:
                if e.status != 409:
                    raise
            obj = self.resource(kind, name)
        if obj is None or obj.metadata.deletion_timestamp:
            raise Failure()
        return obj

    def claim(self, data, pinned):
        obj = self.ensure("pvc", {
            "metadata": self.metadata(data),
            "spec": {"storageClassName": self.storage_class, "accessModes": [self.mode],
                     "resources": {"requests": {"storage": os.getenv("DATA_SIZE", "128Mi")}}},
        }, pinned)
        if obj.spec.storage_class_name != self.storage_class or obj.spec.access_modes != [self.mode]:
            raise Failure("workspace_identity_changed", 409)
        return obj.metadata.uid

    def pod(self, workspace, data, profile, previous_uid):
        resources = {"small": {"memory": "64Mi", "cpu": "150m"},
                     "medium": {"memory": "128Mi", "cpu": "300m"}}[profile]
        spec = {
            "automountServiceAccountToken": False, "enableServiceLinks": False,
            "terminationGracePeriodSeconds": 2, "restartPolicy": "Always",
            "securityContext": {"runAsNonRoot": True, "runAsUser": 65532, "runAsGroup": 65532,
                                "fsGroup": 65532, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{
                "name": "workspace", "image": self.image, "imagePullPolicy": "IfNotPresent",
                "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]}},
                "resources": {"requests": {"cpu": "10m", "memory": "24Mi"}, "limits": resources},
                "env": [{"name": "WORKSPACE_ID", "value": workspace}],
                "volumeMounts": [{"name": "data", "mountPath": "/data"}],
            }],
            "volumes": [{"name": "data", "persistentVolumeClaim": {"claimName": data}}],
        }
        if self.node:
            spec["nodeSelector"] = {"kubernetes.io/hostname": self.node}
        # Only the provider creates objects with these names. Adopt a creation
        # after a lost response, checking both ownership and the requested binding.
        metadata = self.metadata(workspace)
        metadata["labels"]["ow-workspace-data"] = data
        if self.spread:
            spec["affinity"] = {"podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [{
                "labelSelector": {"matchLabels": {"ow-workspace-data": data}},
                "topologyKey": "kubernetes.io/hostname",
            }]}}
        obj = self.ensure("pod", {"metadata": metadata, "spec": spec})
        mounts = [v.persistent_volume_claim.claim_name for v in obj.spec.volumes if v.persistent_volume_claim]
        if mounts != [data] or obj.spec.containers[0].image != self.image:
            raise Failure("workspace_identity_changed", 409)
        if previous_uid and obj.metadata.uid != previous_uid:
            raise Failure("workspace_identity_changed", 409)
        return obj

    def remove(self, kind, name, uid):
        kind = {"compute": "pod", "data": "pvc"}[kind]
        obj = self.resource(kind, name, uid)
        if obj is None:
            return True
        if not obj.metadata.deletion_timestamp:
            delete = self.api.delete_namespaced_pod if kind == "pod" else self.api.delete_namespaced_persistent_volume_claim
            try:
                delete(name, self.namespace, body=client.V1DeleteOptions(
                    preconditions=client.V1Preconditions(uid=obj.metadata.uid)), _request_timeout=6)
            except ApiException as e:
                if e.status != 404:
                    raise
        return False

    def file(self, workspace, physical_id, operation):
        # Use a separate API client for exec. The streaming transport temporarily
        # replaces the REST transport on the Kubernetes Python client instance.
        with client.ApiClient() as exec_client:
            api = client.CoreV1Api(exec_client)
            ws = stream.stream(api.connect_get_namespaced_pod_exec,
                workspace, self.namespace, container="workspace", command=["python", "/app/files.py"],
                stdin=True, stdout=True, stderr=True, tty=False, _preload_content=False,
                _request_timeout=8)
            output = ""
            deadline = time.monotonic() + 12
            try:
                ws.write_stdin(json.dumps({"workspace": workspace, "operation": operation}) + "\n")
                while ws.is_open() and time.monotonic() < deadline:
                    ws.update(timeout=1)
                    output += ws.read_stdout(timeout=0)
                    if ws.peek_stderr(timeout=0) or len(output) > 1_048_576:
                        raise Failure()
                if ws.is_open() or ws.returncode != 0:
                    raise Failure()
            finally:
                ws.close()
        reply = json.loads(output)
        if "error" in reply:
            raise Failure("invalid_workspace_request", 400)
        return reply["result"]
