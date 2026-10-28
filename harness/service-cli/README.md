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

Output: `deployment-plan.json`, `REVIEW.md`, `permissions-request.json`,
`resource-graph.json`, and `manifest.json`. The output directory must not already exist. On POSIX systems it
is created with mode 0700 and files with 0600. Plans contain no secret values.
Windows ACL behavior is not qualified by this milestone.

The plan has a stable unique ID, fixed expiry (24 hours by default, `--ttl-hours`
1–72), target scope, budget authorization, bounded proposed topology and a bundle
hash. Inspect/status do not report live cloud resources. Validate checks local
integrity/semantics/expiry only; `applyReady` remains false. Unknown cost estimates
stay null, and a budget is not represented as a hard spending cap.

**Apply/destroy on all clouds remain disabled.** GCP and Azure now support offline
planning with provider-specific target fields:

```sh
ows deploy gcp plan --project my-ows-project --location us-central1 \
  --name my-service --budget 100 --out ./gcp-plan
ows deploy azure plan \
  --tenant 11111111-2222-4333-8444-555555555555 \
  --subscription 66666666-7777-4888-8999-aaaaaaaaaaaa \
  --location westus2 --name my-service --budget 100 --out ./azure-plan
```

AWS plans have two export options:

- `--operator-arn` plus `--infrastructure-config`: generate infrastructure templates,
  the execution role and scoped execution policies. See the
  [infrastructure export guide](../../docs/harness/OWS-AWS-INFRASTRUCTURE-EXPORT.md).
- Paired `--operator-arn` and `--execution-role-arn`: retain an existing executor and
  export identities only.

The imported-role option exports two IAM role policies, an identity-only
CloudFormation template, a review, and `bootstrap.sh`. This path requires an existing customer-reviewed CloudFormation
execution role. It is not the complete infrastructure installer. Both modes produce
a script that is customer-run explicitly with `--apply-identities`; `ows` never executes it. It has
only been tested against a fake AWS CLI, not live IAM. Ordinary plans emit no script.
GCP/Azure bootstrap generation is still design-only.

See [cloud identities and bootstrap](../../docs/harness/OWS-CLOUD-IDENTITY-DESIGN.md)
for commands, exact scope, prerequisites, cloud-specific implementation order, and
remaining execution-policy work.

[Specification](../../docs/harness/OWS-DEPLOYMENT-SPEC.md) ·
[Implementation plan and ledger](../../docs/harness/OWS-DEPLOYMENT-PLAN.md)

## Read-only AWS preflight

```sh
ows deploy aws preflight --plan ./aws-review --profile customer-login \
  --out ./aws-preflight
```

This uses AWS CLI v2 to check identity, prerequisites, supported pins, observed quota
headroom, published fixed costs, guard artifact bytes and generated IAM policy
syntax/security findings. Reports go into a new directory outside the immutable
plan. Required blocked checks return exit 1; the JSON report explains them. A passing
report still has `applyReady: false`.

After identity bootstrap, `--check-setup-role` verifies temporary assumption of the
exact setup role with identity-only session permissions. Credentials are not saved.
See the [preflight guide](../../docs/harness/OWS-AWS-PREFLIGHT.md) for timeouts,
permissions, pricing assumptions and remaining deployment gates.
