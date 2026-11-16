# owcluster

A Rust CLI over the OpenWorker Service SDK. Build with:

```sh
cargo build --locked --release -p openworker-service-cli
```

Start with the [Deployment options](../../docs/harness/guides/deployment.md) and
[local Docker package](../../deploy/service/README.md). Run `owcluster --help` for commands.
JSON is written to stdout, errors to stderr; `watch` produces NDJSON.

## Offline cloud deployment plans

The initial deployment-command milestone is available independently of service
login. It makes no cloud calls and does not require AWS credentials:

```sh
owcluster deploy aws plan --account 123456789012 --region us-west-2 \
  --name my-service --budget 100 --out ./my-service-plan
owcluster deploy inspect --plan ./my-service-plan
owcluster deploy validate --plan ./my-service-plan
owcluster deploy status --plan ./my-service-plan
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

Ordinary `deploy apply/destroy` remain closed. The explicit experimental AWS
lifecycle path below is available for a separately authorized trial. GCP and Azure support offline
planning with provider-specific target fields:

```sh
owcluster deploy gcp plan --project my-ows-project --location us-central1 \
  --name my-service --budget 100 --out ./gcp-plan
owcluster deploy azure plan \
  --tenant 11111111-2222-4333-8444-555555555555 \
  --subscription 66666666-7777-4888-8999-aaaaaaaaaaaa \
  --location westus2 --name my-service --budget 100 --out ./azure-plan
```

AWS plans have two export options:

- `--operator-arn` plus `--infrastructure-config`: generate infrastructure templates,
  the execution role and scoped execution policies. See the
  [AWS deployment guide](../../docs/harness/guides/aws.md).
- Paired `--operator-arn` and `--execution-role-arn`: retain an existing executor and
  export identities only.

The imported-role option exports two IAM role policies, an identity-only
CloudFormation template, a review, and `bootstrap.sh`. This path requires an existing customer-reviewed CloudFormation
execution role. It is not the complete infrastructure installer. Both modes produce
a script that is customer-run explicitly with `--apply-identities`; `owcluster` never executes it. It needs
to be reviewed for your account policies before use. Ordinary plans emit no script.

Run `owcluster deploy --help` and each subcommand's help for accepted arguments.

## Read-only AWS preflight

```sh
owcluster deploy aws preflight --plan ./aws-review --profile customer-login \
  --out ./aws-preflight
```

This uses AWS CLI v2 to check identity, prerequisites, supported pins, observed quota
headroom, published fixed costs, guard artifact bytes and generated IAM policy
syntax/security findings. Reports go into a new directory outside the immutable
plan. Required blocked checks return exit 1; the JSON report explains them. A passing
report still has `applyReady: false`.

After identity bootstrap, `--check-setup-role` verifies temporary assumption of the
exact setup role with identity-only session permissions. Credentials are not saved.
See the [AWS deployment guide](../../docs/harness/guides/aws.md) for timeouts,
permissions, pricing assumptions and remaining deployment gates.

## Experimental AWS lifecycle and shared Kubernetes installer

`owcluster deploy aws lifecycle` prepares separate locked state, verifies original stack
receipts and guard health, reconciles uncertain operations, publishes immutable
images and emits an identity-bound installation handoff. Cloud mutations require
`--allow-experimental-cloud`; new creates also need fresh matching preflight.
`owcluster deploy cluster` shares the local installer with platform preparation, private
Secret staging, managed-database checks and teardown receipts. Native CLI runtime
dependencies are AWS CLI/Helm/kubectl/Docker/tar, not Python or Node.

See [AWS deployment](../../docs/harness/guides/aws.md) and
[deployment options](../../docs/harness/guides/deployment.md). Use an owned trial
with an explicit budget and independent cleanup before production deployment.

`owcluster` is the primary command. `owcluster` remains an alias, using the same commands, configuration and saved deployment state. Existing `.ows` paths and `OWS_*` settings remain valid.

## Optional management UI and trace storage

Select these features in `deployment.yaml` for the AWS/Azure `up` commands, or
change them on an existing installation:

```sh
owcluster management-ui enable --state ./deployment-state/installation --port 8444
owcluster management-ui disable --state ./deployment-state/installation
owcluster management-ui status --state ./deployment-state/installation
owcluster observability enable --state ./deployment-state/installation --retention-days 14
owcluster observability disable --state ./deployment-state/installation
owcluster observability status --state ./deployment-state/installation
```

`--state` is the original installer journal, using its Kubernetes credentials and
verified cluster identity. UI and trace storage are independent. Disabling tracing
retains data and leaves security audit and separately configured exporters intact.
The CLI saves feature changes and reconciles lost Helm responses without repeating
unknown upgrades. See [setup, UI image, private HTTPS and database options](../../deploy/service/up/README.md#management-ui-and-trace-storage).

Complete [Azure, AWS and local kind reference files](../../deploy/service/examples/README.md)
put deployment choices and advanced account/cluster inputs in one YAML. The CLI
generates internal values/installer files; replace the documented placeholders
before use. `local-docker` and `remote-k8s` are explicit roadmap modes, not
qualified installers. New cloud configs omit budget; optional estimated-cost
ceilings use `up --plan-only --max-estimated-cost-usd`.
