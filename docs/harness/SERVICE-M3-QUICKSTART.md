# Submit agents with ows

`ows` is the command-line client for OpenWorker Service. It uses the same
REST/SSE API as the Rust and TypeScript SDKs; it does not run another agent loop.
Start with the [local container setup](../../deploy/service/README.md).

## Connect

For the local package, pass `--config .ow-service/cli.json` to every command.
For another service:

```sh
ows context add development --url https://service.example --namespace team
ows --context development login --token-file /private/service-token --ca-file /private/service-ca.pem
ows context use development
ows whoami
```

The token file must be private (0600 on Unix). Alternatively use
`--token-env OW_SERVICE_TOKEN`. Credentials are never command-line token values.
Contexts store file/env references, CA trust and the verified service ID. Login
validates existing credentials; human identity login is not included yet.

## Publish and activate

Save `profile.json`:

```json
{"id":"development@1","provider":"openshell","cpu":1,"memoryMB":512,"sharing":"shared","approvalMode":"ask"}
```

Save `agent.json`:

```json
{
  "id":"builder@1",
  "instructions":"Build and verify the requested project using the available tools.",
  "tools":["read_file","write_file","run_shell"],
  "runtimeProfiles":["development@1"],
  "defaultRuntimeProfile":"development@1"
}
```

```sh
ows profile deploy --file profile.json --idempotency-key profile-v1
ows profile activate development@1 --alias default --idempotency-key activate-profile-v1
ows agent deploy --file agent.json --idempotency-key builder-v1
ows agent activate builder@1 --alias builder --idempotency-key activate-builder-v1
```

Definitions accept JSON or YAML. Publishing/activation require administrator
permission. Revisions are immutable; changing `builder` later does not alter an
existing session. Release tools must exist in the installed trusted bundle.

## Submit and follow

For a live-model deployment, put the task in `task.txt` and run:

```sh
ows submit --agent builder --project game --message-file task.txt --idempotency-key game-first-task
ows operation wait OPERATION_ID
ows watch --session SESSION_ID --run RUN_ID
ows status --session SESSION_ID
```

The initial response contains `operationId` inside `operation.id`, plus
`sessionId`, `environmentId`, `runId` and `submissionId` inside `operation`.
These identities are reserved before provisioning. The controller creates the
session and queues its first task in one database transaction. Retry an uncertain
submission using the same key and body; changing the body under that key fails.
Use `--wait` with `submit` to wait for provisioning in the same command.

`watch` writes NDJSON. Save event cursors and reconnect with `--after CURSOR`.
Ctrl-C disconnects the watcher; it does not cancel the run. A pending approval or
question is an interruption, not successful task completion. Inspect `status`,
answer it, and watch again. Errors retain recovery IDs/keys where applicable.

```sh
ows approve --session SESSION_ID --interaction INTERACTION_ID --allow once --idempotency-key approval-1
# --allow session grants this tool for this session; --allow deny refuses it.
ows answer --session SESSION_ID --interaction INTERACTION_ID --answer-file answer.txt --idempotency-key answer-1
ows cancel --session SESSION_ID --run RUN_ID --idempotency-key cancel-1
ows control --session SESSION_ID --receipt CONTROL_REQUEST_ID
ows send --session SESSION_ID --message-file follow-up.txt --idempotency-key follow-up-1
```

Controls use a separate request path from SSE. The CLI reads the pending
interaction and supplies its durable policy/schema binding. It does not manufacture
approvals from stale display text. Cancellation is explicit and does not replay
unknown tool actions.

To share a compatible environment with another agent, supply
`--environment-id ENVIRONMENT_ID` to `submit`. Otherwise the default RuntimeProfile
provisions one; `--runtime-profile PROFILE_ID` overrides that default. Shared agents
need separate directories/worktrees and coordinated ports. Conversation state stays
private to each session.

## Clean up

```sh
ows close --session SESSION_ID
ows environment delete ENVIRONMENT_ID --idempotency-key delete-environment-1
ows environment get ENVIRONMENT_ID
```

Close refuses unfinished work, pending controls or live attachments. Environment
deletion requires admin permission and all sessions closed. It returns `deleting`
while the private controller works; repeat the same key to observe the result.
A returned generated `idempotencyKey` must be retained when you omitted your own.
The API never receives OpenShell credentials just to perform cleanup.

## SDK equivalent

After `Harness.connect(...)`, both SDKs expose combined admission:

```typescript
const operation = await service.submit("builder", "Build a small game", {
  project: "game",
  idempotencyKey: "game-first-task",
});
// Save operation.id, sessionId, environmentId and runId before waiting.
await service.waitOperation(operation.id);
```

```rust,ignore
let operation = service.submit(
    "builder",
    SessionOptions { project: Some("game".into()), ..Default::default() },
    "Build a small game",
    "game-first-task",
).await?;
```

See [M2 quickstart](SERVICE-M2-QUICKSTART.md) for connecting, release/model
configuration and attaching sessions. Service capabilities explicitly advertise
`combinedSubmission`; older services reject this helper rather than silently
falling back to a non-atomic two-request sequence.
