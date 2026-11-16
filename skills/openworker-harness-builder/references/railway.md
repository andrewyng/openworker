# Deploy an SDK app on Railway

Use this track for a long-running Node web service. By default, deploy the
frontend and HTTP API together: one Node process serves the built UI and API
routes, and manages the Rust harness as a child process in the same container.
This gives the browser one origin for pages, authentication and streamed replies.
Keep the user's framework and product; adapt the assets rather than replacing
an existing application. Preserve an existing frontend/backend split, or use
separate services when the user needs independent deployment or scaling.
Read [Web](web.md) and [SDK integration](sdk.md) for authentication, session
ownership and the SDK lifecycle.

```text
Browser ── HTTPS ──> Node UI + HTTP API ── private stdio ──> Rust harness
                         │                                   │
                   app records/uploads                 durable sessions
                         └──────── volume /data ─────────────┘
```

Start with one service and one replica. This is a managed harness process,
not an OpenWorker service cluster. No OpenWorker checkout, Rust compiler or
OpenShell installation is needed for this application-owned-input example.
Enable coding tools or isolated execution only when the product requires them.

## Prepare the application

Pin the SDK version from `installation.json` and commit the app's lockfile.
Keep the SDK in production dependencies. Start one harness per application
process with an absolute `OPENWORKER_STATE_DIR`, for example `/data/harness`.
Configure its provider from runtime secrets, create the agent, and await
`agent.ready()` before accepting agent work. Re-supply memory-only provider
credentials on every restart. Pass the state directory into `Harness.start`;
setting an environment variable does not replace a hard-coded application path.
A laptop's localhost model is not reachable from Railway: use an authorized
cloud/proxy provider or a reachable model service. Network model endpoints must
use HTTPS; the SDK accepts plain HTTP only on loopback.

Implement these application behaviors before deploying:

- Listen on `0.0.0.0` and `Number(process.env.PORT ?? 8080)`.
- Provide `/health` without user authentication, returning no secrets. Return
  success only after startup completes; return 503 while starting or draining.
  If hostname filtering is enabled, permit Railway's healthcheck hostname.
- Authenticate agent endpoints and authorize document/session lookup. A session
  reference is not a login credential. A shared bearer token is only suitable
  for a private single-user demo.
- Stream responses through the application's API and check the final run status.
  On browser disconnect, follow the product's cancellation policy explicitly.
- On SIGTERM/SIGINT, mark readiness false, stop new work, cancel or finish active
  runs within the configured shutdown window, then await `harness.shutdown()`.
  Use a bounded shutdown deadline. Start Node directly, not behind a shell or
  `npm start` wrapper that may swallow signals.

Persist `session.reference()` with the application's owner/conversation record,
then use `agent.resumeSession(reference)` after a restart. The harness persists
history; it does not persist the application's in-memory document map, uploads,
or ownership records. Put those in the mounted directory or an application DB.
For PDF Notes, retaining only the harness database is insufficient to restore
an uploaded document's context. Apply explicit retention to both stores.

## Build the container

Copy/adapt [Dockerfile](../assets/railway/Dockerfile) and
[.dockerignore](../assets/railway/.dockerignore) into the app, preserving existing
project files. The Dockerfile expects a committed package-lock.json, an optional
`build` script, and `dist/server.js`; change the entrypoint to the real server.
Do not ship a development server. Review the build context for application-specific
secret/data paths and monorepo workspace dependencies. Also exclude secrets and
local state from Git/source uploads; Docker ignore only controls the image context.

Install optional dependencies inside the glibc Linux build stage. This selects
the Linux native runtime automatically. The slim-image runtime stage installs
`libssl3` and CA certificates, which the native helper needs; do not copy a macOS/Windows helper or
host node_modules. The final image needs neither Rust nor Electron. Keep model
keys out of Docker build arguments, layers and committed configuration.

Use `docker build -t my-openworker-app .` and test with a disposable named volume,
runtime-only variables and a loopback model fixture if no live key is available.
A local Docker test establishes container behavior, not live Railway qualification.

## Set up the Railway target

Use the user's selected workspace, project, environment and service. Existing
authorization to deploy there is sufficient; if the target or spending scope is
missing, establish it before creating paid resources or exposing a service.
Do not delete/recreate an existing service or volume to make a deployment succeed.

Railway detects a root `Dockerfile`. Set the service root correctly for a monorepo.
Configure the following on the chosen service before its first public deployment:

| Setting | Value or decision |
| --- | --- |
| Build | Dockerfile in the app's service root |
| Start command | Use the image's direct Node command, or the app's equivalent |
| Healthcheck | `/health`; allow enough time for SDK initialization |
| Volume | Mount at `/data`; create application state directories at startup |
| Harness state | `OPENWORKER_STATE_DIR=/data/harness` |
| Application state | e.g. `/data/app`; persist conversation ownership and uploads |
| Provider | Exact model ID and provider secrets as runtime service variables |
| Access | App authentication; a private demo may use `APP_ACCESS_TOKEN` |
| Replicas | One for this SQLite/volume recipe |
| Shutdown | Give the app a drain window longer than its own shutdown deadline (for example, 20 seconds for a 10-second app deadline) |

Railway volumes are mounted as root. For the provided non-root image, Railway
currently documents `RAILWAY_RUN_UID=0` as its compatibility setting. This runs
the app as root inside the container. If the deployment requires a non-root
process, use an approved volume-ownership initialization instead and test it;
do not assume Dockerfile `chown` changes an already mounted Railway volume.

Volumes are available at runtime, not during image build or pre-deploy commands.
A volume-backed service can have downtime during replacement. This recipe does
not promise zero-downtime failover or shared SQLite across replicas. Do not enable
sleeping for background agent work without verifying the lifecycle behavior.
Set explicit service CPU and memory limits suited to the app, and inspect workspace
usage after deployment. For a temporary test, agree on whether to keep it running
or remove it after qualification; do not delete a demo the user wants to try.
For horizontal scaling, decide on external storage, session routing and workers
or an OpenWorker service deployment as a separate architecture change.

## Deploy and verify

Use an existing Railway CLI, or follow its official installation instructions.
For an authorized, existing target, the usual interactive flow is:

```sh
railway login
railway link
railway service
railway status
railway up
railway logs
```

Check the selected project/environment/service from `status` before uploading.
For a new target, create the approved project/service and volume first. Supply
secrets through Railway's variable editor or approved secret workflow, not shell
history or chat. Generate a public domain only after authentication is configured.
For non-interactive commands, read the installed CLI's `--help` for explicit target
flags rather than guessing them.

Do not generate new legacy railway.json/railway.toml config-as-code templates.
Current Railway docs deprecate those; for reproducible provisioning use the
current `railway config`/IaC workflow and review its plan for the chosen target.
The Docker assets here do not create a volume, set service variables or provision
infrastructure by themselves.

Check the deployed URL's readiness, rejected unauthorized requests, streamed
response and terminal status. Exercise stop/disconnect behavior. Redeploy the
same service with its volume retained, authenticate again and resume the same
owned conversation. A healthcheck only gates deployment; add ongoing monitoring
if the product requires it. Report the URL, exact release, checks performed and
anything tested only locally. Do not claim a live deployment without checking it.

## Platform references

Consult these before changing platform-specific commands or settings:

- [Docker builds](https://docs.railway.com/builds/dockerfiles)
- [Volumes and permissions](https://docs.railway.com/volumes)
- [Healthchecks](https://docs.railway.com/deployments/healthchecks)
- [CLI](https://docs.railway.com/cli)
- [Infrastructure as Code](https://docs.railway.com/infrastructure-as-code)
- [Legacy config status](https://docs.railway.com/config-as-code/reference)
