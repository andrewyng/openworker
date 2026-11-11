# Deployment examples

Reference files for the configuration formats accepted by `ows`. The cloud
examples enable the management UI and trace storage, select two fixed-size nodes,
and use a six-hour disposable trial with a US$100 planning limit.

These files contain deliberately fake identities, paths, versions and image
references. Replace them and complete preflight before approving provisioning.
No credentials are embedded. A planning limit is not a hard cloud spending cap.
Trial expiry deletes workload data; use these examples only for disposable data.

| Target | Application/deployment choices | Installer/account choices | Command |
| --- | --- | --- | --- |
| Azure | [azure/deployment.yaml](azure/deployment.yaml) | [azure/setup.json](azure/setup.json) | `ows deploy azure up --config azure/deployment.yaml --keep-access` |
| AWS | [aws/deployment.yaml](aws/deployment.yaml) | [aws/setup.json](aws/setup.json) | `ows deploy aws up --config aws/deployment.yaml --keep-access` |
| Existing local kind cluster | [local-cluster/service-values.json](local-cluster/service-values.json) | [local-cluster/installer.json](local-cluster/installer.json) | `ows deploy local plan --config local-cluster/installer.json --out .ows/local` |

Commands assume you copied this directory and run from its root. Neither cloud
CLI login nor this example folder replaces the required provisioning permissions.
No `ows deploy local up` command exists yet; the local installer targets an existing
kind cluster, OpenShell release and staged PostgreSQL/TLS fixtures. It does not
create a Kubernetes cluster or database for you.

## Azure

1. In `deployment.yaml`, set your subscription, region and desired node count/size.
   `Standard_D4as_v7` is a supported reference choice, subject to regional
   availability, subscription restrictions and quota checks.
2. In `setup.json`, use the same subscription (`account`) and region. Set
   `setupObjectId` to the object ID of your signed-in deployment identity in the
   subscription's Entra tenant; it is not an application/client ID. `User` is for
   interactive Azure CLI login; an approved service principal uses
   `ServicePrincipal` and its own object ID.
3. Supply the existing workload/control resource groups, Entra administrator group,
   SSH public key (no comment), versioned Key Vault password reference and a
   digest-pinned cleanup image in the independent control registry. Replace the
   example secret version, image digest, available AKS version and Ubuntu image
   version. The `203.0.113.10` operator IPv4 is a documentation address: replace it
   with the authorized operator's real IPv4. The SSH private key stays with you;
   select it using `advanced.azure.sshPrivateKeyFile` in deployment YAML or your
   SSH agent. Key Vault needs RBAC and ARM template deployment access.
4. Point `release` to the absolute path of the prepared **Linux AMD64**
   `release.json`, including a pinned `service-ui` image.

The resource groups, vault/secret, administrator group and independent cleanup
image are one-time prerequisites in the current installer. The reviewed bootstrap
handles its supported identity/grant creation; this example does not mean every
Azure prerequisite is created automatically. See the [installer prerequisites](../up/README.md#one-time-prerequisites).

## AWS

1. Set your account, region and authenticated AWS CLI profile in `deployment.yaml`.
2. Use the matching account/region and approved installer role in `setup.json`.
3. Replace the availability zones and verify the pinned Kubernetes, node, database
   and add-on versions against your region. These are reference pins from the
   supported infrastructure format, not a claim of current regional availability.
4. Replace `guardCode` with the actual versioned S3 object and checksum of this
   release's packaged cleanup guard. Replace `operatorAccess.amiId` with a reviewed
   regional ARM64 AMI supporting the SSM operator bridge; the example AMI is fake.
   The bridge keeps the Kubernetes management API private. Install the AWS Session
   Manager plugin for this mode.
5. Point `release` to the absolute path of a prepared **Linux ARM64** `release.json`
   that includes a pinned `service-ui` image.

Both clouds use `advanced.setupProfile: ./setup.json`. That path is resolved
relative to `deployment.yaml`. Paths **inside** the profile/release files must be
absolute with the current loader. The setup profile is an OpenWorker configuration
file, not another cloud login or a place to store access tokens.

Export your model key in your shell before deployment; the examples reference
`ANTHROPIC_API_KEY` without embedding its value. To deploy without a model key,
remove `models` and configure the provider later. Installation readiness does not
perform an actual model call or create an agent.

Run a read-only review first, using the same arguments with `--plan-only`. Remove
that flag to proceed through the CLI's scoped approval gates. The command saves
`connection.json` with the API connection, UI preview URL, certificate and
port-forward instructions. Keep the private route open using `--keep-access`.
Register/activate a RuntimeProfile and agent release, then submit your agent using
the service SDK.

The samples create disposable trials. For persistent deployments omit `trial` and
review a realistic monthly planning budget and a separate data-retention/teardown
policy. The current destructive teardown route refuses persistent data.

## Existing local kind cluster

Edit both JSON files together:

- Replace the absolute kubeconfig, chart and values paths, and select your explicit
  `kind-*` context. The current local installer does not target Docker Desktop or
  arbitrary private-cluster contexts directly.
- Replace **every** example service/runner/UI digest with an actual locally imported
  or pullable image for the node architecture. The repeated `a` digest is fake.
- Use a dedicated service namespace; keep its current `ow-service-m4-` prefix for
  this local installer. Match it in `managementUI.serviceUrl`.
- Update the existing OpenShell namespace, release and fullname together with
  `config.openshell.endpoint` / `tls_server_name`. Its client TLS Secret must be
  available to the trusted installer.
- Stage the fixture PostgreSQL deployment (named `postgres`) and TLS/model Secrets
  in the installer's owned namespace. `fixtureDatabase: true` identifies this local
  test database; it does not install one or disable database TLS verification.
  `ca.crt` in the API TLS Secret must trust the service certificate, whose SANs
  include localhost and the configured API Service DNS name.

The existing lower-level sequence is:

```sh
ows deploy local plan --config local-cluster/installer.json --out .ows/local
ows deploy local apply --state .ows/local --accept-plan PLAN_DIGEST --openshell-only
# Stage the disposable PostgreSQL fixture in the newly owned namespace.
ows deploy local secrets --state .ows/local --accept-plan PLAN_DIGEST \
  --input /absolute/private/installation-secrets.json
ows deploy local apply --state .ows/local --accept-plan PLAN_DIGEST
ows management-ui status --state .ows/local
ows observability status --state .ows/local
```

Use the actual `planDigest` printed by `plan`. Copy [the placeholder secrets-file shape](local-cluster/installation-secrets.example.json)
to a private location outside your checkout, restrict it to mode0600, and fill in
the actual certificate/key/model values there. Never commit the filled file. The
installer reads OpenShell client TLS from the existing gateway Secret itself;
those credentials are not part of this file. See the [shared installer CLI](../../../harness/service-cli/README.md)
for command context.
The existing reference scripts are `deploy/service/prepare-local-install.mjs` and
`deploy/service/kubernetes-local.mjs`; inspect their required paths before use.
They are qualification helpers, not a standalone local-cluster bootstrap command.

A local cluster has no cloud node count or cloud budget in its installer JSON:
capacity belongs to the existing cluster. UI/storage toggles use the same
`ows management-ui` and `ows observability` commands as cloud installations.
