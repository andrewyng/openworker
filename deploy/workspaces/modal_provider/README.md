# Modal workspace provider

Use a Modal Sandbox for each workspace and a Modal v2 Volume for each project. Sessions sharing a workspace use one sandbox. Giving the reviewer a separate workspace with the builder’s data ID mounts the same volume in a second sandbox. File writes and edits commit before the service returns success. A sandbox reloads its mount before the next file operation.

## Run the service

Use a dedicated Modal App in the intended environment. An operator can create an empty App once using the Modal Python SDK:

```python
import modal
modal.App.lookup("website-workspaces", environment_name="main", create_if_missing=True)
```

From `deploy/workspaces`, build `docker build --target modal-service -t ow-modal-workspaces .`. Supply these settings to a **single** service instance:

| Setting | Purpose |
| --- | --- |
| `MODAL_APP_NAME`, `MODAL_ENVIRONMENT` | Select the existing App and environment. |
| `MODAL_TOKEN_ID`, `MODAL_TOKEN_SECRET` | Operator credentials for the service. The sandbox receives only its workspace ID. |
| `WORKSPACE_SERVICE_TOKEN` | A separate, random service bearer token (at least 32 characters). |
| `/state` | A durable directory, writable by UID 65532, for SQLite data and the writer lock. Keep one store per registration. |
| `/tls/tls.crt`, `/tls/tls.key` | Server certificate and private key readable by the service. Use a SAN matching its HTTPS endpoint. |
| `IDLE_SECONDS`, `MODAL_SANDBOX_SECONDS` | Idle reclamation after the last session detaches (default 600 seconds) and the Modal sandbox lifetime (default 3600 seconds). Active sessions can recover onto replacement compute using their original volume. |

The service listens on HTTPS port 8443. Its runtime hosts need a private route to that endpoint. The operator owns the App and supplies credentials from a secret store. Project volumes persist until the app explicitly deletes data. Back up the state directory for recovery.

## Register and use it

Add a named registration and its allowed application principal to the OpenWorker service configuration. Existing Kubernetes registrations can coexist in the same map. The older `kubernetes_workspaces` configuration name remains supported. Use one spelling for the map in each config.

```json
{
  "workspace_services": {
    "websites": {
      "endpoint": "https://workspaces.example.internal",
      "tokenEnv": "WEBSITE_WORKSPACE_SERVICE_TOKEN",
      "caFile": "/etc/openworker/workspace-ca.crt"
    }
  },
  "workspace_grants": { "websites": ["website-builder"] }
}
```

The existing SDK calls work with this registration:

```ts
const sites = await service.workspaces.startProvider({ name: "websites" });
const project = await sites.getOrCreate({ key: authorizedProject.id });
const session = await builder.newSession({ workspaceId: project.workspaceId });
```

The provider accepts the existing `podProfile` values `small` (0.25 CPU, 256 MiB) and `medium` (0.5 CPU, 512 MiB). The application coordinates concurrent edits to shared data. If a mutation loses its acknowledgement, inspect the file before retrying it.

## Test with a disposable registration

Run the [website-builder SDK test](../../../harness/typescript-sdk/examples/website-builder/README.md#reproduce-the-end-to-end-check) with the provider map in `OW_WORKSPACE_SERVICES`, a disposable PostgreSQL database and an empty application state directory. Set `IDLE_SECONDS=35`. Use a fresh barrier directory shared by the test and this verifier. Run the verifier alongside the Rust test using the same Modal profile:

```sh
python modal_provider/verify_website_builder.py --state-dir "$WORKSPACE_STATE_DIR" \
  --barrier "$OW_WEBSITE_BARRIER" --evidence "$EVIDENCE_FILE"
```

The verifier checks the actual sandbox IDs and volume tags across both reviewer modes, idle reclamation and app restart. After the run, stop the test workspaces, delete their data through the service, and stop the disposable Modal App.
