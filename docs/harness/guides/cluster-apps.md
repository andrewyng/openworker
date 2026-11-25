# Run multiple apps on one OpenWorker cluster

The cluster admin issues a separate backend token for each app. Each app owns its agent definitions and sessions. Genie can register a `builder`, and a second app can register its own `builder` on the same cluster. Each app handles sign-in and decides which end users can access its sessions.

## Register a model and give it a stable profile

Set `OPENWORKER_MODEL_REGISTRY_KEY` on the trusted service API and workers to the same base64-encoded 32 random bytes. With Helm, set `secrets.modelRegistry` to the name of a Kubernetes Secret containing that value in the `registry-key` entry. Back up this encryption key alongside the database. Rotating an inference credential uses the command below and preserves the registry encryption key.

With an admin CLI context selected, save the following provider definition as `spark.yaml`:

```yaml
name: spark
api: chat-completions
baseUrl: https://inference.example.com/v1
contextLimit: 32768
```

```sh
owcluster model-provider register spark.yaml --key-file /path/to/inference-key
owcluster model-provider set-profile coding --provider spark --model qwen3-coder-next
owcluster model-provider inspect spark
```

Use `responses`, `chat-completions` or `anthropic` for the corresponding wire API. HTTPS is the default. For an operator-controlled private network, a literal RFC1918 IPv4 HTTP endpoint requires `allowPrivateHttp: true`. Loopback HTTP is also supported. The endpoint must be reachable from the worker. Provider keys are encrypted in PostgreSQL. The Management UI, SDK responses and project tools receive no model registry secrets.

Register a new provider name to change a route’s URL, wire protocol or context limit. Point `coding` at the new provider or model to change the default for **new sessions**. Existing sessions keep the resolved provider and model. The provider’s context limit caps the compaction window and the requested output after the runtime estimates input size. App grants can allow a named profile or specific provider/model pairs. Granting a profile lets the admin change its default without redeploying the app.

In the Management UI, open **Settings → Models** to create a managed profile or change its model using an installed connection or an enabled provider.

```sh
owcluster model-provider list
owcluster model-provider rotate spark --key-file /path/to/new-inference-key
owcluster model-provider disable spark
```

A worker fetches the current credential for each new model request. A disabled provider rejects further requests. Database-managed credentials support cluster worker inference, including `sandboxedtools`. A model profile can also target an existing deployment-configured model connection. With `sandboxedloop`, point it at the connection configured for the native OpenShell inference provider. That sandbox receives its inference credential through OpenShell.

## Issue an app token

In **Management UI → Settings → Access → Issue token**, choose **Application**. Enter the app name, for example `genie`, and the allowed model profiles or provider/model pairs. Enter any additional external tools the app can use, for example the installed web search tool. Copy the token into the app backend’s secret store. The app principal is `app_genie`.

The cluster’s built-in file read, write/edit and shell tools require no separate app grant. A release still declares its tools. Workspace isolation, the execution profile, approval requirements and sandbox network policy still apply. A third-party tool named `read_file` gets no exemption. Restrict shell network egress in the sandbox if an app should have no outbound network access.

Grant the app principal access to its intended runtime profile using that profile’s `allowedPrincipals`. For a shared definition, both apps also need access to its runtime profile and the tools and models it uses.

## Publish a revision from the app backend

```ts
import { Harness } from '@openworkerai/harness-sdk';

const service = await Harness.connect({
  url: process.env.OPENWORKER_URL!,
  namespace: 'production',
  credentials: { getToken: async () => process.env.OPENWORKER_APP_TOKEN! },
});

const builder = await service.publishAppAgent('builder', {
  instructions: 'Build the requested page in the project workspace.',
  tools: ['coding.read_file', 'coding.write_file'],
  runtimeProfiles: ['project@1'],
  defaultRuntimeProfile: 'project@1',
  modelProfile: 'coding',
}, { idempotencyKey: 'builder-first-revision' });

const session = await builder.newSession({ project: 'homepage' });
```

Publishing the same name with a new idempotency key creates a private revision. To run the latest visible revision later, use `await service.appAgent('builder')`. Existing session references keep their original release. A cluster admin can share a specific app revision from the **Agents** page. An authorized second app can use `await service.appAgent('builder', 'app_genie')` to run that revision. Its sessions belong to the second app. The second app cannot edit Genie's definition or read Genie's sessions.

## Rotate an app token during an existing team session

In **Settings → Access**, select **Rotate** on the app credential. Store the replacement token on the backend. Rotation revokes the old token and preserves the app principal and grants. Use **Revoke** to invalidate a token without issuing a replacement.

With the new credential, resume the lead session using its saved reference. An existing team must explicitly authorize future child admissions with the current token:

```ts
const lead = await service.resume(savedLeadReference);
const team = await lead.resumeTeam();
await team.reauthorize();
```

The service verifies the same principal, scope, role, permitted environments and active release/profile before accepting the change. A later child admission checks authority again. A completed or denied child request keeps its original outcome. Use a new spawn request to create the next reviewer.
