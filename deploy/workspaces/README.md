# Kubernetes workspace provider

A named provider creates a compute pod for each active workspace and a PVC for each data ID. Multiple sessions attached to one workspace use the same pod. A second workspace opened with the same data ID gets its own pod mounting the same claim. Session close releases a session’s access. Stopping a workspace releases its pod and preserves the project data for a later visit.

## Install a registration

Build both targets from this directory. Supply versions from a registry reachable by your cluster, or load the images into the local nodes. Use immutable images for a persistent installation.

```sh
docker build --target runner -t "$RUNNER_IMAGE" .
docker build --target service -t "$SERVICE_IMAGE" .
```

Choose a dedicated registration name such as `website-workspaces`. The renderer creates a control namespace for its single controller and metadata PVC, and a workload namespace for its project pods and data PVCs. The controller’s Kubernetes service account can manage pods and claims and execute the bundled file helper only within its workload namespace. Keep that namespace exclusive to this provider. File helpers run as a non-root user with a read-only image and no service account token.

Create the control namespace, then supply a strong bearer token and a TLS certificate whose SAN covers the service endpoint. Files below are operator-provided. The token file should contain at least 32 printable characters with no trailing newline.

```sh
name=website-workspaces
kubectl create namespace "$name-control"
kubectl -n "$name-control" create secret generic workspace-auth --from-file=token="$TOKEN_FILE"
kubectl -n "$name-control" create secret tls workspace-tls --cert="$TLS_CERT" --key="$TLS_KEY"

python3 render.py --name "$name" \
  --service-image "$SERVICE_IMAGE" --runner-image "$RUNNER_IMAGE" \
  --metadata-class "$METADATA_CLASS" --data-class "$SHARED_STORAGE_CLASS" \
  --mode ReadWriteMany | kubectl apply -f -
kubectl -n "$name-control" rollout status deployment/controller
```

Use a storage driver that implements the selected mode. `ReadWriteMany` supports independently scheduled pods with a suitable shared filesystem. For node-local development, select `--mode ReadWriteOnce --pin-node <kubernetes-hostname-label>`. That permits separate workspace pods to mount the same volume on one node. `--mode ReadWriteOncePod` or `--single-mount` rejects a second active workspace on the same data with `workspace_shared_mount_unsupported`. Multiple sessions can still attach to the first workspace.

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

Existing workspace sessions support the scoped list, read, write and edit tools. The app coordinates concurrent edits. File operations run through Kubernetes exec using a fixed helper command and a JSON request over stdin. A lost reply leaves the result uncertain. Inspect the files before issuing another edit.

## Recovery and cleanup

Project keys, data IDs, compute IDs, attachments, pending deletes and Kubernetes UID pins live in SQLite on the controller-state PVC. The controller records creation intent before calling Kubernetes. Repeating an interrupted open uses the same resource names. If a workspace pod disappears, opening or recovering it starts a replacement on the recorded PVC. The logical workspace ID survives replacement. A missing or replaced previously bound PVC requires operator attention.

After the last session detaches, explicitly call `stopWorkspace(workspaceId)` or allow the idle timeout to release compute. Set `--idle-seconds` to configure it (default 600, zero disables automatic reclamation). Call `deleteData(dataId)` to delete the PVC after all its workspaces stop. The StorageClass reclaim policy controls physical PV disposal. The controller resumes pending deletes after restart and uses UID preconditions. Removing the provider’s namespaces is a destructive uninstall.

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
