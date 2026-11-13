# Deploy your app on Railway

Host your frontend and API together, with the harness running beside your Node server.

```text
Browser → Railway HTTPS → Node server (pages + API)
                              │
                              ├─ managed Rust harness → model provider
                              └─ persistent volume → sessions + app records
```

Start with one service and one replica. This is application hosting: no separate
OpenWorker service cluster or OpenShell installation is required for a document
assistant that uses application-supplied inputs.

## Build with the skill

Install the [builder skill](../../../skills/README.md), then ask:

```text
Use openworker-harness-builder to deploy this web app on Railway. Serve the UI
and API from one Node service. Keep model keys in runtime secrets, persist
sessions on a volume, require authentication for agent endpoints, and test
streaming and recovery before deployment.
```

The skill includes the detailed Railway recipe and Docker assets. It adapts to
an existing app rather than requiring an OpenWorker repository checkout.

## Prepare your server

- Listen on `0.0.0.0` and `process.env.PORT`.
- Install the SDK as a production dependency, with optional runtime packages enabled.
- Keep API keys in Railway runtime variables; never put them in frontend code or the image.
- Pass an absolute directory on the mounted volume to `Harness.start({ stateDir })`.
- Persist your own user/document-to-session mapping as well as harness state.
- Authenticate agent endpoints and authorize each session lookup.
- Shut down the harness on termination and handle stream cancellation explicitly.

Use a glibc Linux image, such as Debian Node, with CA certificates and the native
runtime's OpenSSL dependency. Install the runtime inside that image; do not copy
your laptop's executable into a Linux deployment.

## Deploy PDF Notes

From the example directory, verify the application locally first:

```sh
npm run setup
npm test
npm run web
```

Create a Railway service from your application repository. For the monorepo
example, set its root directory to `harness/examples/quickstart` and use the
included Dockerfile. Configure:

| Setting | Value |
| --- | --- |
| Model credentials | `OPENAI_API_KEY`, supplied as a secret |
| Model | `OPENWORKER_MODEL`, an available Responses model with PDF support |
| Demo access | `APP_ACCESS_TOKEN`, a private random value |
| Volume mount | `/state` |
| Session directory | `OPENWORKER_STATE_DIR=/state` |
| Networking | Railway supplies `PORT`; the container listens on all interfaces |

Ensure the mounted `/state` is writable by the image's `node` user (UID 1000).
Use your platform's approved volume-ownership setup and check startup logs; the
[skill recipe](../../../skills/openworker-harness-builder/references/railway.md)
discusses Railway-specific volume permissions.

Generate a Railway domain and open the UI over HTTPS. Enter the application access
token in its settings. This demo token is for a single-user instance; a multi-user
product needs individual login and ownership checks.

The PDF Notes demo keeps document handles in memory, so a restart requires re-upload.
Its accepted session data persists on the volume. To offer seamless reopening,
persist the app's handle/session mapping and resume that session.

## Verify your deployment

Upload a small PDF, observe streaming before completion, ask a follow-up and test
cancellation. Check unauthorized requests are rejected. For a product that resumes
conversations, replace the deployment and confirm the same saved session reference
can be reopened from persistent storage.

The single-service Railway recipe has been tested live with an earlier published
SDK. Test the exact current package and your application before launch; that
qualification does not establish multi-replica or zero-downtime behavior.
See the [complete deployment recipe](../../../skills/openworker-harness-builder/references/railway.md).
