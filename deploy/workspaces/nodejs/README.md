# Node.js workspace image and private previews

Build the Linux `openworker` ToolRunner for the cluster's architecture from the same OpenWorker revision as the runtime. Copy that executable to the root of a temporary build context containing `files.py`, `runner_call.py`, and this `nodejs/` directory. From that context run:

```sh
docker build -f nodejs/Dockerfile -t "$NODE_IMAGE" .
```

Publish the image to your operator registry and configure its immutable digest. The preinstalled Vite starter runs without downloading packages. The project volume mounts at `/data`. Dependencies are installed in `/opt/starter/node_modules`, visible to project imports through `/node_modules`. The ToolRunner, Vite binary and starter belong to the read-only image. Temporary files and the process-manager socket use compute-local storage. App-specific images can derive from this image and install their starter dependencies at build time.

Build the private gateway from `deploy/workspaces/gateway` with `docker build -t "$GATEWAY_IMAGE" .`. The existing workspace Helm chart accepts:

```yaml
imageProfiles:
  nodejs: registry.example.com/website-runner@sha256:REPLACE_WITH_IMAGE_DIGEST
preview:
  domain: preview.example.com
  gatewayImage: registry.example.com/workspace-preview@sha256:REPLACE_WITH_IMAGE_DIGEST
  serviceCASecret: workspace-service-ca
  tlsSecret: workspace-preview-tls
```

Create the referenced secrets in the controller namespace. The first contains `ca.crt` for the workspace service; its server certificate must cover `workspaces.<namespace>.svc`. The second contains the gateway server certificate and key (`tls.crt` and `tls.key`) valid for the private TLS endpoint your app connects to. Use a CNI that enforces the supplied network policies. The ClusterIP `workspace-preview` serves every workspace and every preview port. File-only installations can leave previews and image profiles unset.

## Open and run

```ts
const sites = await service.workspaces.startProvider({
  name: "websites",
  options: { imageProfile: "nodejs", podProfile: "medium", previewPortCount: 5 },
});
const site = await sites.getOrCreate({ key: authorizedProject.id });
const session = await builder.newSession({ workspaceId: site.workspaceId });
```

The builder release must select `process_start`, `process_status`, `process_logs`, and `process_stop` from its first-party `mcp-coding` server to use these tools. Start the dev server with `exec vite --host 0.0.0.0 --port 5100 --strictPort`. Later tool calls use the returned process ID. A lost launch reply has an uncertain outcome. Inspect the running server before issuing another launch. A replacement pod has a new process manager: launch the dev server again against the recovered files.

`site.previews` contains one `{port, url, accessToken}` per slot. Zero slots (the default) omits this field. A requested count of 5 returns ports 5100–5104; the maximum is 15. Pass `options.previewPortCount` in `getOrCreate` to override the handle default. Repeating a compatible open returns the same slots. A conflicting explicit count for running compute fails. Reopening stopped project data without a declared count restores its stored count.

## Browser and application boundary

The app backend owns user/project authorization. Configure wildcard DNS and TLS for the preview domain to reach the app's preview relay on an origin separate from its API. Keep the full workspace response server-side. The browser receives only each slot's URL. Set up app authentication for each preview origin; keep authentication cookies host-scoped and isolated from generated pages' API privileges.

For each HTTP request or WebSocket upgrade:

1. Authenticate the user and authorize access to the project and exact slot. Reject unexpected browser origins on upgrades.
2. Connect over the shared private TLS route to `workspace-preview`. Set the HTTP Host (or HTTP/2 authority) to the slot hostname from the stored URL. Set Authorization to Bearer followed by its stored accessToken. Forward the relative path, query string and required WebSocket protocol headers. Never accept a client-supplied upstream destination or forward browser credentials to a workspace.
3. Stream the response or relay the upgraded WebSocket. A 503 with Retry-After means the preview is unavailable, including while the port has no listener. A 410 means the handle is stale. Reopen through the authorized SDK path to obtain the new mapping. Invalid credentials return 403.

The provider resolves the current pod UID and IP for each request and the gateway rechecks the physical target after TCP connect. Long-lived connections are revalidated periodically. The workspace receives no preview bearer token or authentication cookie. A pod replacement rotates all hostnames and capabilities even if the logical workspace ID survives. Grant access to the workspace service and preview gateway only to trusted application networks.

## Qualify a disposable installation

Use a new pair of namespaces, a digest-pinned Node profile, the chart's preview gateway, and a disposable Postgres database. Build the TypeScript SDK first. Set `OW_WORKSPACE_SERVICES`, `OW_PG_TEST_URL` and the token named in the provider map as in the [website-builder qualification guide](../../../harness/typescript-sdk/examples/website-builder/README.md). The test uses the registration name `websites`. When running the Rust fixture on macOS, set `OW_WORKSPACE_TEST_RUNNER` to a matching Linux runner identity file.

Set `OW_PREVIEW_GATEWAY` to the HTTPS loopback port forwarding to the private gateway, and `OW_PREVIEW_CA` to its CA certificate. Both the browser verifier and the Rust test must see the same fresh, private directory in `OW_PREVIEW_BARRIER`. Checkpoint files contain short-lived test credentials. Keep them outside source control.

1. Provide a disposable TLS key and certificate for the browser fixture in `OW_TEST_TLS_KEY` and `OW_TEST_TLS_CERT`. Make Playwright and Chrome available; `OW_TEST_PLAYWRIGHT` can point to the installed module. Run `node deploy/workspaces/verify_browser_preview.mjs` from the repository root. Its loopback test relay uses sample project authentication. It checks an actual Vite WebSocket update and verifies that the page's time origin survives the edit.
2. From `harness/`, run `cargo test --locked -p openworker-server-reference --features workspace-reference --lib workspace_preview_ports -- --ignored --nocapture`.
3. At the `replace.json` checkpoint, read the workspace ID, remove the registration prefix, and delete just that trial workspace pod in the workload namespace. Wait for its deletion, then create the empty `replace.ok` file in the barrier directory. The test checks old routes and exercises stop, reopen and deletion through the SDK.
4. Remove the disposable installation, verify volume reclamation, and discard the private checkpoint directory.
