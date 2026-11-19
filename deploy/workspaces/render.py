"""Render one isolated registration as a Kubernetes List; see README.md."""
import argparse
import json
import re


def resources(args):
    name = args.name
    control, workloads = name + "-control", name + "-pods"
    def obj(kind, obj_name, ns=None, api="v1", **body):
        meta = {"name": obj_name}
        if ns:
            meta["namespace"] = ns
        return {"apiVersion": api, "kind": kind, "metadata": meta, **body}

    labels = {"app": "ow-workspaces"}
    yield obj("Namespace", control)
    yield obj("Namespace", workloads)
    yield obj("ServiceAccount", "controller", control)
    yield obj("Role", "controller", workloads, "rbac.authorization.k8s.io/v1", rules=[
        {"apiGroups": [""], "resources": ["pods", "persistentvolumeclaims"], "verbs": ["get", "create", "delete"]},
        {"apiGroups": [""], "resources": ["pods/exec"], "verbs": ["get", "create"]},
    ])
    yield obj("RoleBinding", "controller", workloads, "rbac.authorization.k8s.io/v1",
              roleRef={"apiGroup": "rbac.authorization.k8s.io", "kind": "Role", "name": "controller"},
              subjects=[{"kind": "ServiceAccount", "name": "controller", "namespace": control}])
    yield obj("NetworkPolicy", "isolate-workspaces", workloads, "networking.k8s.io/v1",
              spec={"podSelector": {}, "policyTypes": ["Ingress", "Egress"]})
    yield obj("PersistentVolumeClaim", "controller-state", control, spec={
        "storageClassName": args.metadata_class, "accessModes": ["ReadWriteOnce"],
        "resources": {"requests": {"storage": "128Mi"}},
    })
    env = {"WORKLOAD_NAMESPACE": workloads, "RUNNER_IMAGE": args.runner_image,
           "STORAGE_CLASS": args.data_class, "DATA_MODE": args.mode,
           "ALLOW_SHARED_COMPUTE": str(not args.single_mount).lower(), "IDLE_SECONDS": str(args.idle_seconds)}
    if args.pin_node:
        env["PIN_NODE"] = args.pin_node
    yield obj("Deployment", "controller", control, "apps/v1", spec={
        "replicas": 1, "strategy": {"type": "Recreate"}, "selector": {"matchLabels": labels},
        "template": {"metadata": {"labels": labels}, "spec": {
            "terminationGracePeriodSeconds": 5, "serviceAccountName": "controller", "enableServiceLinks": False,
            "securityContext": {"runAsNonRoot": True, "runAsUser": 65532, "runAsGroup": 65532,
                                "fsGroup": 65532, "seccompProfile": {"type": "RuntimeDefault"}},
            "containers": [{
                "name": "controller", "image": args.service_image, "imagePullPolicy": "IfNotPresent",
                "ports": [{"containerPort": 8443}],
                "readinessProbe": {"httpGet": {"path": "/health", "port": 8443, "scheme": "HTTPS"}, "periodSeconds": 2},
                "resources": {"requests": {"cpu": "20m", "memory": "64Mi"},
                              "limits": {"cpu": "500m", "memory": "256Mi"}},
                "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]}},
                "env": [{"name": k, "value": v} for k, v in env.items()] + [
                    {"name": "WORKSPACE_SERVICE_TOKEN", "valueFrom": {"secretKeyRef": {"name": "workspace-auth", "key": "token"}}}],
                "volumeMounts": [{"name": "state", "mountPath": "/state"},
                                 {"name": "tls", "mountPath": "/tls", "readOnly": True},
                                 {"name": "tmp", "mountPath": "/tmp"}],
            }], "volumes": [{"name": "state", "persistentVolumeClaim": {"claimName": "controller-state"}},
                            {"name": "tls", "secret": {"secretName": "workspace-tls", "defaultMode": 0o440}},
                            {"name": "tmp", "emptyDir": {"sizeLimit": "16Mi"}}],
        }},
    })
    yield obj("Service", "workspaces", control, spec={
        "selector": labels, "ports": [{"port": 443, "targetPort": 8443}], "type": "ClusterIP",
    })


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--service-image", required=True)
    p.add_argument("--runner-image", required=True)
    p.add_argument("--data-class", required=True)
    p.add_argument("--metadata-class", required=True)
    p.add_argument("--mode", choices=["ReadWriteMany", "ReadWriteOnce", "ReadWriteOncePod"], default="ReadWriteMany")
    p.add_argument("--pin-node")
    p.add_argument("--single-mount", action="store_true")
    p.add_argument("--idle-seconds", type=int, default=600)
    args = p.parse_args()
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,40}[a-z0-9]", args.name):
        p.error("expected a short DNS label for name")
    if args.mode == "ReadWriteOnce" and not args.pin_node:
        p.error("ReadWriteOnce sharing requires --pin-node")
    print(json.dumps({"apiVersion": "v1", "kind": "List", "items": list(resources(args))}, indent=2))


if __name__ == "__main__":
    main()
