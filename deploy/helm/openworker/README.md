# OpenWorker chart

Deploys independently scaled API and worker pods with an internal TLS Service.
Requires an immutable harness image, reviewed reference configuration and
existing Secret references. Does not install OpenShell, PostgreSQL, S3, cluster
prerequisites or certificates. No public Ingress is created.

See the source repository's `deploy/helm/README.md` for configuration, tested
pins, setup/upgrade procedures, local qualification and production limits.
The local driver outside this chart is development-only. API authorization and
tool/sandbox policy remain separate; shared sandboxes are not tenant isolation.
