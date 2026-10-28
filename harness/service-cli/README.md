# ows

A Rust CLI over the OpenWorker Service SDK. Build with:

```sh
cargo build --locked --release -p openworker-service-cli
```

Start with the [CLI quickstart](../../docs/harness/SERVICE-M3-QUICKSTART.md) and
[local Docker package](../../deploy/service/README.md). Run `ows --help` for commands.
JSON is written to stdout, errors to stderr; `watch` produces NDJSON.

## Offline cloud deployment plans

The initial deployment-command milestone is available independently of service
login. It makes no cloud calls and does not require AWS credentials:

```sh
ows deploy aws plan --account 123456789012 --region us-west-2 \
  --name my-service --budget 100 --out ./my-service-plan
ows deploy inspect --plan ./my-service-plan
ows deploy validate --plan ./my-service-plan
ows deploy status --plan ./my-service-plan
```

Output: `deployment-plan.json`, `REVIEW.md`, `permissions-request.json`, and
`manifest.json`. The output directory must not already exist. On POSIX systems it
is created with mode 0700 and files with 0600. Plans contain no secret values.
Windows ACL behavior is not qualified by this milestone.

The plan has a stable unique ID, fixed expiry (24 hours by default, `--ttl-hours`
1–72), target scope, budget authorization, bounded proposed topology and a bundle
hash. Inspect/status do not report live cloud resources. Validate checks local
integrity/semantics/expiry only; `applyReady` remains false. Unknown cost estimates
stay null, and a budget is not represented as a hard spending cap.

**Apply, destroy, GCP and Azure execution are not implemented yet and fail without
calling cloud tools.** Permission requirements are not grantable IAM policies.
Runnable bootstrap scripts will follow the actual provider permission/template
implementation; this milestone intentionally emits none.

[Specification](../../docs/harness/OWS-DEPLOYMENT-SPEC.md) ·
[Implementation plan and ledger](../../docs/harness/OWS-DEPLOYMENT-PLAN.md)
