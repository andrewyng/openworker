# Management UI: complete-stack local qualification

**Passed on 27 October 2026**, using a fresh local kind deployment on Linux ARM64,
Kubernetes v1.36.4, and the existing NVIDIA OpenShell gateway.

The tested path was:

```text
Browser (HTTPS)
  → local TLS ingress → management UI pod
  → service API (HTTPS + scoped bearer credential)
  → PostgreSQL → service worker/controller
  → OpenShell gateway (mTLS) → sandboxed ToolRunner
```

Model responses were deterministic. Provisioning, tool execution, file effects,
permissions, restarts, cancellation, capacity changes and cleanup were real.
No paid model request or cloud resource was used. “OpenShift” in the request was
interpreted as the existing OpenShell integration; Red Hat OpenShift was not tested.

## Verified

- Published and activated a RuntimeProfile and agent release through the browser.
- Signed in as a scoped runner, submitted a task, and provisioned a real OpenShell
  execution environment. Management controls were absent for that runner.
- Refreshed a parked approval and restarted the API. The interaction identity and
  pending decision remained intact; browser event updates reconnected.
- Approved the actual shell action. Restarted the UI during execution, signed in
  again, and recovered the same session and environment without restarting its work.
- Approved the separate read-file request under the ask profile. An independent
  shell read verified exactly **one** persisted append across the restarts.
- Cancelled an in-flight shell command. The worker applied the cancellation, retained
  an explicit `outcome_unknown` tool response in model history, and marked the run
  `interrupted`. The command was not replayed. Cancellation does not imply rollback
  or confirmed termination of every remote side effect.
- Requested two workers through the UI and observed two ready Kubernetes workers.
- Closed the session, deleted its remote environment, revoked the runner credential,
  and removed the dedicated namespace/PVC. The three original suspended sandboxes
  remained. Copied gateway credentials and generated private input/token files were
  removed; cached images and sanitized evidence were retained.

## Corrections during qualification

The initial test ingress needed all Nginx temporary directories redirected to its
writable `/tmp` volume because the root filesystem is read-only. The local helper
now does this.

Two initial test assumptions were corrected without replaying the write: the read
also needed approval, and cancellation during remote execution could correctly end
as `interrupted` with an unknown outcome. The first session was resumed using its
durable identity. A second, fresh deployment then passed the entire corrected test
in one uninterrupted run, in approximately 51 seconds.

No harness runtime fix was needed. The tested harness/UI implementation is commit
`098d5f4e`. Exact image digests, session IDs and checks are in the
[sanitized evidence](evidence/service-ui-kubernetes-20261027.json).

## Reproduce and next scope

Use the [management UI guide](../../harness/service-ui/README.md#complete-stack-local-kubernetes-qualification),
`deploy/service/ui-kubernetes-local.mjs`, and `deploy/service/qualify-ui-kubernetes.mjs`.
The qualifier refuses to overwrite an existing run journal and never automatically
replays mutations or tools after an uncertain outcome.

This qualifies the local complete stack. AWS/GCP deployment, live model traffic,
x86-64 images, browser SSO, and Red Hat OpenShift still need separate evidence.
The UI currently uses one replica with expiring in-memory browser sessions; UI
restarts require sign-in while durable agent work continues.
