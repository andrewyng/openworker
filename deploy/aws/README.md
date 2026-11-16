# AWS deployment development tools

This directory contains experimental, account-scoped deployment and cleanup
helpers, plus fixtures used to test the service on AWS. These scripts are not a
generic production installer. Some intentionally bind to a particular owned
experiment; inspect their configuration and ownership checks before use.

For application deployment, start with the [AWS guide](../../docs/harness/guides/aws.md)
and the [ows CLI](../../harness/service-cli/README.md). Setup identity and workload
permissions are separate. Use reviewed, project-scoped identities and explicit
account/region targeting; do not give agent workloads installer credentials.

## Local checks

The deterministic tests exercise ownership checks, fixture validation, bounded
cleanup and report handling without provisioning cloud resources:

```sh
node --test harness/task-desk/server.test.mjs deploy/aws/cleanup.test.mjs deploy/aws/qualification.test.mjs deploy/recovery/backup.test.mjs
```

Live execution additionally requires authenticated AWS access, an owned deployment,
reviewed permissions, budget/expiry controls and an independent cleanup path.
Local tests do not establish those prerequisites. Do not run cleanup or provisioning
commands against shared/customer resources.

## Files and reports

Infrastructure templates, image builders and workload fixtures remain with the
code that consumes them. Runtime credentials, Terraform state and generated
reports belong in private, ignored state directories. Never commit those outputs.
See [service deployment](../service/README.md) for the reusable container and
Helm configuration.
