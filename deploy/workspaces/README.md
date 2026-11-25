# Kubernetes workspace provider

A named provider creates a compute pod for each active workspace and a PVC for each data ID. Multiple sessions attached to one workspace use the same pod. A second workspace opened with the same data ID gets its own pod mounting the same claim. Session close releases a session’s access. Stopping a workspace releases its pod and preserves the project data for a later visit.

## Install a registration with Helm

Build both targets from this directory. Supply versions from a registry reachable by your cluster, or load the images into the local nodes. Use immutable images for a persistent installation.

```sh
docker build --target runner -t "$RUNNER_IMAGE" .
docker build --target service -t "$SERVICE_IMAGE" .
```

Use one pair of dedicated namespaces for each registration. The controller runs in the control namespace. Its service account manages workspace pods and claims and executes the bundled file helper in the workload namespace. Workspace pods run as a non-root user without a service account token. Enable Kubernetes NetworkPolicy enforcement in the cluster to enforce workload network isolation.

Create both namespaces and two secrets before installing. The certificate SAN must cover the actual endpoint used by the OpenWorker runtime. The bearer token file must contain at least 32 printable characters with no trailing newline. Keep both secrets outside Helm values and release history.

```sh
kubectl create namespace websites-control
kubectl create namespace websites-pods
kubectl -n websites-control create secret generic workspace-auth --from-file=token="$TOKEN_FILE"
kubectl -n websites-control create secret tls workspace-tls --cert="$TLS_CERT" --key="$TLS_KEY"
```

For local Kubernetes, select a node from `kubectl get nodes -L kubernetes.io/hostname` and use the local storage class provided by your installation:

```sh
helm upgrade --install websites ./chart -n websites-control -f examples/local.yaml \
  --set-string placement.pinNode="$NODE_HOSTNAME" \
  --set-string images.controller="$SERVICE_IMAGE" --set-string images.runner="$RUNNER_IMAGE" --wait
```

For standard **EKS with EC2 workers**, use the same chart with [EKS values](examples/eks.yaml). Provide an EBS CSI storage class for the single controller's SQLite PVC and an EFS CSI class with dynamic access point provisioning for project PVCs. The [sample storage classes](examples/eks-storage.yaml) are operator-managed. Set the real EFS filesystem ID in a copy before applying it. The EFS filesystem needs reachable mount targets, NFS access from the workers and the CSI driver's IAM permissions. Provide at least two schedulable nodes with different hostname labels. The controller and runner images must be pullable by those nodes. [EBS CSI setup](https://docs.aws.amazon.com/eks/latest/userguide/ebs-csi.html) · [EFS CSI setup](https://docs.aws.amazon.com/eks/latest/userguide/efs-csi.html)

```sh
helm upgrade --install websites ./chart -n websites-control -f examples/eks.yaml \
  --set-string images.controller="$SERVICE_IMAGE" --set-string images.runner="$RUNNER_IMAGE" --wait
```

The EKS example sets `placement.spreadSharedData` so Kubernetes schedules newly created workspaces sharing a data ID on different nodes. Sessions sharing a workspace still use one pod. Each additional active workspace on the same data needs another eligible node. Use `false` when cross-node placement is optional. The project PVC size on EFS is a Kubernetes request, not a per-project storage quota.

The supplied service is a private ClusterIP. When OpenWorker runs outside the cluster, provide an authenticated HTTPS endpoint reachable over your private network, or use a port forward for a trial. Only the workspace service and its workspace pods run in the target cluster.

Uninstalling the chart retains its controller-state PVC, the pre-created namespaces and the workspace PVCs. For a reinstall against retained state, set `metadata.existingClaim=controller-state` and use the original workload namespace. For permanent removal, stop active workspaces and delete their data through the provider before removing the dedicated namespaces. For EFS, verify access point and backing directory cleanup according to the driver's configuration.

The earlier `render.py` installer remains available.

Use a storage driver that implements the selected mode. `ReadWriteMany` supports independently scheduled pods with a suitable shared filesystem. For node-local development, select `data.accessMode=ReadWriteOnce` and set `placement.pinNode`. That permits separate workspace pods to mount the same volume on one node. `data.accessMode=ReadWriteOncePod` or `data.singleMount=true` rejects a second active workspace on the same data with `workspace_shared_mount_unsupported`. Multiple sessions can still attach to the first workspace.

Run the installer with separate names and credentials for independent registrations. A second registration called `research-workspaces` has its own namespaces and metadata store. Protect and back up the controller-state PVC along with project data.

## Configure OpenWorker

Add the following fragment to the API, controller and worker runtime configurations. Install the corresponding bearer token environment variables and CA files on those processes. The Rust client requires HTTPS and disables redirects. Provider-specific application options select operator-supported profiles.

```json
{
  "kubernetes_workspaces": {
    "websites": {
      "endpoint": "https://workspaces.website-workspaces-control.svc",
      "tokenEnv": "WEBSITE_WORKSPACES_TOKEN",
      "caFile": "/etc/openworker/workspaces/ca.crt"
    },
    "research": {
      "endpoint": "https://workspaces.research-workspaces-control.svc",
      "tokenEnv": "RESEARCH_WORKSPACES_TOKEN",
      "caFile": "/etc/openworker/workspaces/ca.crt"
    }
  },
  "workspace_grants": {
    "websites": ["website-builder"],
    "research": ["research-app"]
  }
}
```

The app authorizes the user’s project access and calls the service SDK:

```ts
const workspaces = await service.workspaces.startProvider({ name: "websites" });
const build = await workspaces.getOrCreate({ key: project.id + "/builder" });
const main = await builder.newSession({ workspaceId: build.workspaceId });

// Share compute and files with the builder.
const peer = await reviewer.newSession({ workspaceId: build.workspaceId });

// Alternatively, give the reviewer independent compute and the same files.
const review = await workspaces.getOrCreate({
  key: project.id + "/reviewer", dataId: build.dataId, options: { podProfile: "medium" },
});
const separate = await reviewer.newSession({ workspaceId: review.workspaceId });
```

The default workspace supports scoped list, read, write and edit tools. For managed dev servers and per-port private URLs, see the [Node.js ToolRunner and preview guide](nodejs/README.md). The app coordinates concurrent edits. File operations run through Kubernetes exec using a fixed helper command and a JSON request over stdin. A lost reply leaves the result uncertain. Inspect the files before issuing another edit.

## Recovery and cleanup

Project keys, data IDs, compute IDs, attachments, pending deletes and Kubernetes UID pins live in SQLite on the controller-state PVC. The controller records creation intent before calling Kubernetes. Repeating an interrupted open uses the same resource names. If a workspace pod disappears, opening or recovering it starts a replacement on the recorded PVC. The logical workspace ID survives replacement. A missing or replaced previously bound PVC requires operator attention.

After the last session detaches, explicitly call `stopWorkspace(workspaceId)` or allow the idle timeout to release compute. Set the chart value `idleSeconds` to configure it (default 600, zero disables automatic reclamation). Call `deleteData(dataId)` to delete the PVC after all its workspaces stop. The StorageClass reclaim policy controls physical PV disposal. The controller resumes pending deletes after restart and uses UID preconditions. Removing the provider’s namespaces is a destructive uninstall.

## Run the Kubernetes integration tests

Use two disposable registrations with empty workload namespaces and a disposable PostgreSQL database. Name the registrations `ow-m4-websites` and `ow-m4-research`. Enable shared mounts on the first and set `--single-mount` on the second. The verifier replaces the websites controller and builder pod during the run.

Build the TypeScript SDK and export these variables for the Rust test process:

- `OW_K8S_WORKSPACES` points to a JSON file containing the `websites` and `research` objects from the registration map above. Export the token variables named in that file.
- `OW_K8S_BARRIER` points to a fresh, empty directory writable by the SDK test and the verifier.
- `OW_PG_TEST_URL` selects the disposable database. Set `OW_NODE` if Node is outside the process search path.

From the repository root, start the verifier in one terminal:

```sh
python3 deploy/workspaces/verify_recovery.py --registration ow-m4-websites \
  --kubeconfig "$KUBECONFIG" --barrier "$OW_K8S_BARRIER" \
  --health-url "$WEBSITES_ENDPOINT/health" --ca "$CA_FILE" --evidence "$EVIDENCE_FILE"
```

In a second terminal, run the SDK scenario:

```sh
cd harness
cargo test --locked -p openworker-server-reference --features workspace-reference \
  --lib kubernetes_workspace_recovery -- --ignored --nocapture
```

To test interrupted provisioning, interrupted deletion and idle reclamation separately, run `python3 deploy/workspaces/verify_intents.py` from the repository root after the SDK scenario finishes. Set `M4_REGISTRATION=ow-m4-websites`, `M4_KUBECTL`, `M4_KUBECONFIG`, `M4_ENDPOINT`, `M4_TOKEN_FILE` and `M4_CA` for that disposable registration. This check temporarily changes pod quota and idle timeout and holds a test PVC during deletion. It expects the backing StorageClass to reclaim deleted volumes.

The [website builder example](../../harness/typescript-sdk/examples/website-builder/README.md) exercises both reviewer modes, app restart after idle reclamation and separate user projects.

For the EKS website-builder test, set the trial release's `idleSeconds=35` and add `--eks-controller-namespace websites-control` to the physical verifier command in the example. It checks the bound controller PV's EBS driver, each project PV's EFS driver, and two different node names for Alice's separate workspaces. A same-node result fails qualification.

The [Modal workspace provider](modal_provider/README.md) serves the same workspace API using Modal Sandboxes and Volumes.
