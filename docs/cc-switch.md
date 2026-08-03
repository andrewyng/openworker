# Route OpenWorker through CC Switch

[CC Switch](https://github.com/farion1231/cc-switch) can run a local
OpenAI-compatible proxy. Configuring it as a model provider lets OpenWorker send
requests to that proxy, while CC Switch remains responsible for selecting an
upstream provider and failing over when one is unavailable.

## Configure the proxy

1. In CC Switch, enable its local proxy and configure the upstream providers and
   failover policy you want to use.
2. Copy the OpenAI-compatible proxy URL from CC Switch's **Proxy** panel. OpenWorker
   adds the required `/v1` suffix when it is omitted, then checks
   `<proxy-url>/models` when you press Test.
3. In OpenWorker, open **Settings → Models → CC Switch (local proxy)** and paste
   that URL into **CC Switch proxy URL**.
4. If proxy authentication is enabled in CC Switch, paste its token into
   **Proxy token**. Otherwise leave that field blank.
5. Press **Test**. A successful test stores the local route; it never stores an
   upstream provider key in OpenWorker.

## Select a routed model

Add a custom model in OpenWorker using the form `ccswitch:<model-id>`, where
`<model-id>` is the model identifier accepted by the active CC Switch route. For
example, if the route accepts `my-agent-model`, add
`ccswitch:my-agent-model`. OpenWorker strips only the `ccswitch:` prefix before
sending the request to the local proxy.

CC Switch can change or fail over its upstream after OpenWorker starts a task.
For that reason OpenWorker treats every `ccswitch:` model as OpenAI-compatible
and uses conservative capabilities: tool calling and streaming are enabled,
while vision, PDF blocks, and parallel tool calls are disabled. This prevents a
mid-run upstream change from silently breaking attachments or tool execution.

## Boundaries

This integration does not read CC Switch request logs, usage data, selected
upstream provider, or failover events. Those remain local to CC Switch. Native
Anthropic and Gemini features are not assumed to survive the compatible proxy;
use their direct OpenWorker providers when those capabilities are required.
