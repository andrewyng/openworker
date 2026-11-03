# Build an agentic web app

Keep the SDK in your server process. The browser talks to your application's API;
it does not receive model credentials, service administrator tokens or private
harness stdio access.

## One container per workspace

Run the TypeScript SDK and its managed Rust child in the same container. Give each
learner or tenant a distinct workspace and state scope. Forward selected streaming
events to the browser. A container does not automatically make unrestricted tools
safe: choose the permissions and sandbox profile deliberately.

The same lifecycle as the [desktop example](desktop.md) applies: start the harness,
register providers, create an agent, create/resume a session and send a message.
Use application-owned authentication before admitting any user request.

## Connect to a service

For shared workers, PostgreSQL-backed recovery and independent execution capacity,
use the service client. API nodes accept authenticated requests; workers run the
agent loop; the execution provider provisions or attaches the tool environment.

[Service architecture and examples](../SERVICE-ARCHITECTURE.html) explains local,
same-node and separate-execution deployments. [Deployment](deployment.md) covers
local qualification and cloud prerequisites. Keep service-token scope tied to the
caller; do not expose a global administrator token to the browser.
