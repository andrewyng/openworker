# Connect InspectionBuddy to an edge service

Use the same app on a workstation while an OpenWorker service runs the agent at
your site. The [example README](../README.md) explains the use case and topology.
To try the application with no Kubernetes at all, use its local development mode.

## 1. Prepare the service

Bring up an OpenWorker service using the
[private Kubernetes guide](../../../../docs/harness/guides/private-kubernetes.md)
and [service chart](../../../../deploy/service/chart/README.md). For a Docker
Compose service connected to an **existing** OpenShell gateway, use the
[local service playbook](../../../../deploy/service/README.md).

The service needs:

- A reachable HTTPS API and a configured OpenShell execution provider with matching runner artifacts.
- A `primary` OpenAI model connection and worker-only model key, with a vision-capable model available to that account.
- Durable PostgreSQL execution storage and optional observability recording for token usage/traces.
- An administrator credential for setup; a separate `owned` operator for the app.

The sample does not create a cluster, replace its TLS certificates or obtain a
cloud identity. Your application server needs network access only to the service
API, not the Kubernetes management API. Match the runner, tool bundle and service
versions as described in the service playbook.

## 2. Register the inspection agent

Run this on a trusted administration machine, from the example directory:

```sh
npm ci --omit=optional
OW_SERVICE_URL=https://agent-service.example.com \
OW_ADMIN_TOKEN_FILE=/absolute/private/admin-token \
NODE_EXTRA_CA_CERTS=/absolute/private/api-ca.crt \
OW_MODEL=gpt-6-luna npm run register-agent
```

Use a public CA instead of `NODE_EXTRA_CA_CERTS` when appropriate. The script
registers `inspection@1`, publishes `inspectionbuddy@1` and activates its alias.
`OW_SERVICE_NAMESPACE`, `OW_RUNTIME_PROFILE`, `OW_AGENT` and `OW_APP_PRINCIPAL`
are configurable; the default app principal is `inspectionbuddy-app`.

The service catalog currently requires a selected tool, so this sample selects
only sandboxed `read_file`; assessments use the supplied image and guidance. It
selects approval bypass and skipped questions for this unattended, read-only
flow. This does not disable sandbox policy. The cluster-free local agent has no
tools at all. Changing a published prompt/model requires a new agent release ID;
changing a RuntimeProfile requires a new profile revision.

## 3. Create app credentials

With your service administrator CLI context, generate private files and a
one-day, owned-scope operator. From this directory:

```sh
umask 077
mkdir -p .private
node --input-type=module <<'JS'
import { randomBytes } from 'node:crypto';
import { writeFileSync } from 'node:fs';
writeFileSync('.private/app-token', randomBytes(32).toString('base64url'), {flag:'wx',mode:0o600});
writeFileSync('.private/operator.json', JSON.stringify({
  principal: 'inspectionbuddy-app', role: 'operator', scope: {kind: 'owned'},
  expires_at: Math.floor(Date.now()/1000) + 86400,
}), {flag:'wx',mode:0o600});
JS
owcluster --config /absolute/private/service-admin.json credentials issue \
  --spec .private/operator.json --out .private/operator-token \
  --idempotency-key inspectionbuddy-operator-v1
```

Use the same principal as registration. Keep the admin credential on the
administration machine. Give the edge application only the operator token,
application token and public service CA. Give the workstation only the application
token (and the public CA if its upstream uses a private CA). Provision renewable
credentials through your organization’s secret system for a longer deployment;
this example reads token files on startup, so restart it after rotating them.

## 4. Start the edge app

Create an ignored `.env` with **absolute paths**:

```dotenv
INSPECTIONBUDDY_MODE=edge
HOST=127.0.0.1
PORT=8080
DATA_DIR=/absolute/persistent/inspectionbuddy
EDGE_APP_TOKEN_FILE=/absolute/private/app-token
OW_SERVICE_URL=https://agent-service.example.com
OW_SERVICE_NAMESPACE=local
OW_SERVICE_TOKEN_FILE=/absolute/private/operator-token
NODE_EXTRA_CA_CERTS=/absolute/private/api-ca.crt
OW_AGENT=inspectionbuddy@1
OW_MODEL=gpt-6-luna
```

Run `npm start`. `OW_MODEL` should match the deployed release; this app setting
does not change the service’s model connection. For private workstation testing,
forward edge port 8080 to workstation loopback and configure frontend mode as
shown in the [README](../README.md#connect-the-workstation-to-an-edge-application).
For remote access, terminate HTTPS and organization authentication in front of
the app; its shared bearer token is only a sample integration boundary.

### Container / Kubernetes option

```sh
# From harness/examples/expertbuddy
# This image needs no optional native runtime: it connects to the service.
docker build -t inspectionbuddy:local .
```

[application.example.yaml](application.example.yaml) is the app deployment,
not an installer for the OpenWorker service. Replace its image with your published
digest, choose your namespace/storage class and service URL, then create the
`inspectionbuddy-application` Secret from the app token, operator token and CA:

```sh
kubectl --context YOUR_CONTEXT -n YOUR_NAMESPACE create secret generic inspectionbuddy-application \
  --from-file=app-token=.private/app-token \
  --from-file=service-token=.private/operator-token \
  --from-file=api-ca.crt=/absolute/private/api-ca.crt
kubectl --context YOUR_CONTEXT -n YOUR_NAMESPACE apply -f deploy/application.example.yaml
```

The manifest runs one non-root replica, drops capabilities, uses a read-only root
filesystem and mounts a 2 GiB application volume. It does not mount a Kubernetes
ServiceAccount token. Keep the single replica/Recreate strategy for this file
store. Provider keys belong only to the trusted harness worker, not this pod or
the OpenShell sandbox.

## Persistence and production boundaries

The application PVC stores photos and inspection records; PostgreSQL separately
stores harness sessions, attachments and traces. Retain both across restarts.
Re-establish local tunnels/port-forwards after a host or forwarded-pod restart.
An app restart reattaches saved runs; it never assumes a request with a lost
receipt failed and blindly submits it again.

For production, add organization identity, per-user/tenant authorization, a
shared database/object store before scaling app replicas, reviewed inspection
standards, retention controls and operational monitoring. Multiple stations and
scoped service credentials are architectural building blocks; this sample is
not a production quality-control certification system.
