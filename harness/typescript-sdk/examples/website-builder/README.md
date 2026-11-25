# Website builder with persistent projects

Alice can build a homepage, ask a reviewer to inspect it, and return to edit the saved page after the app restarts. Bob has a separate project. This browser example uses one Node server and the OpenWorker service SDK. Project files live in the workspace provider.

## Run the example

1. Use an OpenWorker service and SDK built from the workspace-provider branch. Install the [Kubernetes workspace provider](../../../../deploy/workspaces/README.md) as `websites` and grant the app’s service principal access. Choose shared storage or same-node shared mounting for the separate reviewer.
2. Activate a `builder` release with the workspace tools `list_files`, `read_file`, `write_file` and `edit_file`. Activate a `reviewer` release with only `list_files` and `read_file`. Configure a model on the worker and a profile that allows these tools to complete unattended. The example does not implement approval cards.
3. In `harness/typescript-sdk`, run `npm ci && npm run build`. Set `OW_SERVICE_URL`, `OW_SERVICE_NAMESPACE` and `OW_SERVICE_TOKEN` for your service. Generate two demo login tokens, keep them available across restarts, and start the app from that directory.

```sh
export WEBSITE_ALICE_TOKEN="$(openssl rand -hex 32)"
export WEBSITE_BOB_TOKEN="$(openssl rand -hex 32)"
node examples/website-builder/server.mjs
```

Open **http://127.0.0.1:4175**. Enter Alice’s demo token and try these briefs:

| Agent | Brief |
| --- | --- |
| Builder | Create a garden homepage in index.html with a planting calendar. Read the saved file at the end so I can preview it. |
| Reviewer · builder’s workspace | Read index.html and suggest one improvement to its heading. |
| Reviewer · separate workspace, same data | Read index.html from your own workspace and check whether the planting calendar is easy to find. |
| Builder, after restarting the app | Read index.html, change its heading to “Alice’s autumn garden” using an edit, and read back the saved page. |

Refresh the browser to sign in as Bob. His studio starts empty. Give his page the same filename and compare the two previews. The app renders the last successful read of `index.html` in a sandboxed frame. Each request starts and closes its own agent session.

## How the app selects a workspace

| What the app needs | What it passes |
| --- | --- |
| Open Alice’s project today or tomorrow | A stable, server-owned key, `website-builder/garden/builder`, to `getOrCreate`. The provider recovers the data associated with that key. |
| Review in the builder’s running workspace | The builder’s `workspaceId` to the reviewer session. |
| Review on separate compute | A second key, the builder’s `dataId`, and the reviewer’s pod profile to `getOrCreate`. The reviewer session receives the returned `workspaceId`. |
| Open Bob’s project | A different server-owned key derived from his authorized studio project. |

The server checks the bearer token and project ownership on every API request. The browser cannot select a workspace or data ID. The two demo tokens stand in for login, and the fixed project catalog stands in for an app database. The server allows one run per project at a time. A multi-replica app would coordinate concurrent edits across its replicas.

## Reproduce the end-to-end check

Use an empty, disposable `websites` registration on a storage class that supports the two reviewer modes. Set its idle timeout to 35 seconds. Follow the [Kubernetes test setup](../../../../deploy/workspaces/README.md#run-the-kubernetes-integration-tests) for the built SDK, database, provider map, token environment and CA. This test uses only the `websites` entry. Both commands below use a fresh directory, shared between the test and verifier, as `OW_WEBSITE_BARRIER`.

From the repo root, start the read-only physical verifier:

```sh
python3 deploy/workspaces/verify_website_builder.py --kubeconfig "$KUBECONFIG" \
  --namespace "$WORKLOAD_NAMESPACE" --barrier "$OW_WEBSITE_BARRIER" \
  --evidence "$EVIDENCE_FILE"
```

In another terminal from the repo root, run:

```sh
cd harness
cargo test --locked -p openworker-server-reference --features workspace-reference \
  --lib website_builder_app -- --ignored --nocapture
```

The test calls the actual Node app over HTTP with deterministic model tool calls. It stops the app process, waits for Kubernetes to reclaim its idle workspace pods, starts a fresh process, and edits the page. The verifier checks pod UIDs, PVCs, backing volumes and the HTML on disk. Remove the disposable registration and its test data after the run.
