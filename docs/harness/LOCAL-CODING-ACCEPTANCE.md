# Local Kubernetes/OpenShell coding-agent acceptance

October 30, 2026. Branch: `feat/openshell-coding-acceptance`, from GCP installation
integration `85849303`. This is a real-model acceptance exercise, not a benchmark.
GCP provisioning is not involved; its cumulative US$100 allowance is unchanged.

## Goal and sequence

1. Keep the retained observability/NVIDIA demonstration intact. Use a separate
   local service namespace, PostgreSQL state, agent releases and sandbox workspace.
2. Provide an optional coding catalog and pinned development runner containing
   Node, Python, Git and CA tools. Preserve the minimal default service catalog.
   Use the matching existing Linux runtime binary; never mount host homes, Docker
   sockets, provider keys or Kubernetes credentials into the sandbox.
3. Start a real model-driven agent with the coding tools and a sandboxed profile.
   Ask it to build, test and improve a small polished web app with multiple files.
   Use local assets/standard libraries so external package access is unnecessary.
4. Disconnect and reconnect event viewing, then replace this test's API replica
   during execution. Persist operation/session/run IDs before waiting. Never replay
   an uncertain task/tool effect. Inspect the same durable run after reconnect.
5. Ask for a follow-up edit and independent verification. Check application files,
   tests and a running preview, process lifetime, workspace retention and observed
   model/tool accounting. Use a second agent only with explicit sequential shared
   workspace coordination; do not assume edits/ports are conflict-free.
6. Record concrete gaps in tool catalogs, developer images, process control,
   network policy, preview access and recovery UX. Fix reproducible harness issues,
   test the fixes, and merge verified changes back to `openworker-harness-sdk`.

## Boundaries

The isolated test can use approval bypass and auto-skip questions; OpenShell's
filesystem/network restrictions still apply. Model calls originate in the trusted
worker, outside the tool sandbox. No provider key belongs in app source, preview
HTML, public reports or artifact captures. App servers may bind only test ports;
user access stays loopback/private. New local credentials follow the normal
isolated fixture bootstrap; no existing service credential is renewed or widened.
Default runner CPU/RAM and the one-node local cluster are evaluation limits,
not production sizing or physical-node failover evidence.

## Progress ledger

- Existing service/API/worker/controller/PostgreSQL pods are healthy on explicit
  context `kind-ow-server-20261011`. The node advertises 14 CPUs and about 8 GiB RAM.
- Initial gap: the retained runner is intentionally minimal Ubuntu and the default
  service catalog only exposes example read/write/shell tools. Its shell helper
  terminates child processes at call completion and has a 60-second deadline.
  It cannot serve as a persistent app-development process manager.
- The runtime already provides richer coding/process MCP tools. Next: expose those
  through an opt-in service catalog and qualify them inside OpenShell.

### October 30 results

**Outcome: partial acceptance; the long-running task is not qualified yet.** The
installer and opt-in coding packaging are qualified. Two real agent turns exposed
runtime gaps; both remain recorded as failed, without replaying their tool effects.

- Added a separate opt-in service bundle with 15 coding MCP tools and a Linux
  development runner with Node 24, Python, Git and curl. The agent release selected
  14 tools and excluded web fetch; external network access was unnecessary.
- Built/imported local ARM64 images into the existing kind cluster. A separate
  namespace, PostgreSQL fixture/PVC and OpenShell sandbox kept the retained NVIDIA
  demonstration intact. No GCP resources, registry pushes or cloud IAM changes.
- **Fixed a real installation defect:** local live-model configuration copied the
  private model key but never created/mounted the worker Secret. The installer now
  creates it only for live installation; Helm values mount it only in the worker.
  Scripted profiles remain secret-free. Actual deployed Secret bytes match the
  private input, and sandbox pod configuration has no model-key mount or key env.
  OpenShell's own bootstrap plumbing is separate and remains present.
- Fixed configurable local API port reporting. A loopback views helper reconnects
  the API port-forward after replica replacement and serves the standalone UI.
  It does not submit agents, renew tokens or change the default kubecontext.
- **10 Helm/values tests passed**, including worker-only model-key mounting.
  Network-disabled, read-only container MCP smoke passed with 15 discovered tools;
  file, shell and process start/status/stop work across calls in one runner.
- Submitted a real Anthropic `claude-sonnet-5` build through the TypeScript service
  SDK. All file/shell tools executed through the OpenShell runner. Saved admission,
  session, run and environment identities before waiting. Event viewing disconnected
  at the first tool and resumed from its cursor; an API deployment rollout during
  the same run also reconnected successfully. This is API replica replacement,
  **not** worker crash recovery or physical-node failover qualification.
- The agent created eight app/source/test files plus preview metadata for an offline
  Kanban app, Daylight. **39 generated tests passed**, independently rerun in the
  same container. During the first turn, `/health` and HTML routes returned success.
- The original turn reached the fixed **20-model-step** limit before its final
  summary. It remains failed. Its recording has 20 model requests, 19 executed
  tool calls, 601,140 reported input tokens and 39,851 output tokens over about
  338 seconds. These are provider-reported tokens, not unique context or a cost
  measurement. No cache tokens were reported.
- A follow-up retained the same workspace and history, but its previous process
  handle was unknown. The previous endpoint was initially healthy, then remained
  bound while requests timed out. The follow-up failed before editing because a
  tool call did not match its bound input schema. It did not finish the requested
  table/due-date feature. No claim of cross-turn server continuity is made.
- Browser inspection used an explicitly separate **static copy** of the generated
  app, served on Mac loopback. Task creation, moving to Done, summary updates and
  persistence after reload worked. This verifies the app UI, **not** a persistent
  OpenShell preview. Management UI search/timeline showed both real failed turns
  and grouped model/tool accounting. No new Langfuse export was configured here.

Raw prompts, files, tool output, screenshots and tokens remain in ignored private
state. Public evidence contains only aggregate results and image references:
[`evidence/local-coding-20261030.json`](evidence/local-coding-20261030.json).
The failure-path test driver now exits nonzero for an unsuccessful run; a viewing
reconnect never re-admits a task. Its deadline records one cancellation request
for a newly submitted qualification turn; a request receipt is not proof of applied
cancellation. Observing an old run only disconnects the observer at its deadline.

## Next implementation sequence

1. **Configurable turn budgets.** Replace the hardcoded 20-step cutoff with an
   explicit bounded policy, resolved/persisted with the session. Keep cancellation
   and token/time budgets independent. Test exhaustion, restart continuity and a
   build requiring more than 20 model steps. Do not automatically continue a failed
   task or replay an uncertain tool effect.
2. **Correctable argument errors.** A syntactically valid model call with invalid
   schema/path arguments should receive a structured `executed:false` diagnostic
   where appropriate, so the model can correct it. Persist it with the original
   call identity. Keep policy denials distinct; never relax path/network limits,
   approvals or unknown-outcome handling. Today relative-only file paths can yield
   a generic hard-policy denial, whose detailed reason is only in the event record.
3. **Environment-owned process lifecycle and preview.** Define ownership across
   short-lived worker/MCP attachments, stale handles, endpoint reconciliation and
   teardown before advertising persistent dev servers. Test turn completion,
   worker replacement, stop/timeout, port reuse and safe private previews. The
   exact cause of the stalled old listener has not been established; do not label
   it a proven OpenShell defect. A missing `ps` utility also made diagnosis harder.
4. Rerun build → follow-up edit → independent verification → private live preview,
   including API/worker recovery. Then extend to shared-workspace agents and
   allowed package installs with deliberately scoped network policy. Consider
   provider prompt caching after correctness; this run resent substantial context.

## Reproduce the opt-in fixture

Use a dedicated local kind/OpenShell fixture and private configuration as described
in [`deploy/service/README.md`](../../deploy/service/README.md). Select a verified
Linux service base and the **matching** Linux `openworker` executable/architecture.
Build the runner from a minimal directory containing only that executable:

```sh
docker build -f deploy/service/Dockerfile.coding-runner \
  -t openworker-runner:coding /private/path/to/minimal-runner-context
docker build -f deploy/service/Dockerfile.coding-service \
  --build-arg SERVICE_IMAGE=YOUR_VERIFIED_SERVICE_IMAGE \
  -t openworker-service:coding .
node deploy/reference/qualify-coding-runner.mjs openworker-runner:coding
```

Import images with `deploy/helm/load-images.mjs`; use their immutable digests in
local installation config. Set an unused `apiPort` if other fixtures are retained.
Model credentials stay in private input files. `kubernetes-local.mjs install`
creates the worker-only Secret. Never put keys in Helm values or runner context.

The real-model driver is deliberately bound to the named local qualification
namespace. It requires the built TypeScript SDK and a private service state root;
inspect/edit that guard intentionally for a new isolated fixture. It is not a
production job launcher or an automatic cloud provisioner:

```sh
npm --prefix harness/typescript-sdk run build
NODE_EXTRA_CA_CERTS="$PWD/PRIVATE_STATE/service/input/api-ca.crt" \
  node deploy/reference/qualify-coding-service.mjs PRIVATE_STATE start
# Reattach the saved original run without submitting another task:
NODE_EXTRA_CA_CERTS="$PWD/PRIVATE_STATE/service/input/api-ca.crt" \
  node deploy/reference/qualify-coding-service.mjs PRIVATE_STATE observe
# Submit one distinct follow-up only after inspecting the original outcome:
NODE_EXTRA_CA_CERTS="$PWD/PRIVATE_STATE/service/input/api-ca.crt" \
  node deploy/reference/qualify-coding-service.mjs PRIVATE_STATE followup
node deploy/service/local-views.mjs PRIVATE_STATE/service API_PORT UI_PORT
```

Write-once intent/result files prevent accidental repeat submissions. Do not delete
those receipts to retry. Resume/reconcile the original operation instead. Keep the
service's normal token expiry; these helpers do not extend access. Stop loopback
views with Ctrl-C and use only the owned fixture's documented destroy operation
when its diagnostic state is no longer needed.

## October 30 follow-up: continuity fixes qualified

The three gaps above are implemented; see the [plan and progress ledger](CODING-CONTINUITY-PLAN.md)
and [budget/process API contract](TURN-LIMITS-AND-PROCESSES.md). A fresh build
completed after 24 main requests, and an explicitly recovered follow-up completed
with 55 main requests plus two compactions. The original server's handle and PID
survived turn completion and same-image worker replacement. All 45 final generated
tests passed independently. Live browser checks used the actual OpenShell server,
including preserved data through the agent's update and a browser-feedback CSS fix.

A deliberate low-budget turn refused its queued write, gave a short final model
response and left no file. The next turn used the normal defaults. Budget,
validation and process lifecycle events are present in the recording database.
The first build was journaled before recording was enabled; no retroactive metrics
or new Langfuse qualification is claimed. One interrupted follow-up and an earlier
failed admission remain failed. Recovery inspected state before deciding further
work; it did not transparently retry the unknown tool action.

The local connection JSON can now opt into recording at initialization:

```json
"observability": {"schema":"ow_observability","queue_capacity":1024,"retention_days":7}
```

Run the normal explicit migration/bootstrap with this configuration before serving
recordings. Existing installations require migration before updating their pods.
The recorder has its own connection even when it shares the execution database.

`qualify-coding-service.mjs STATE start|observe|followup|recover ATTEMPT` keeps
separate write-once receipts for explicitly numbered trials. `recover` requires an
inspected failed follow-up and first asks the model to reconcile its partial state.
`qualify-coding-limits.mjs STATE ATTEMPT` verifies the stop and following-turn
behavior. Its `resume-budget` / `resume-results` options inspect saved probes and
never submit the already admitted action again. Validation is also covered by the
deterministic runtime suite; a schema-enforcing model need not emit malformed calls.

The local gateway qualification helpers under `harness/runtime/examples/` use
verified mTLS and loopback listeners. The process probe saves creation intent before
provisioning and offers read-only original-object reconciliation; the preview uses
the pinned OpenShell 0.1.3 workspace-scoped forwarding protocol. Private configs
and credentials are never checked in. Sandbox process deadlines still apply.

[Sanitized continuity evidence](evidence/coding-continuity-20261030.json).
