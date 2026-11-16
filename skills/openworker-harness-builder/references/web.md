# Web track

Start with a Node application server that uses the [SDK lifecycle](sdk.md).
The browser calls that server; the Rust harness is a managed child in the same
container. Keep provider credentials, uploads and session ownership on the server.
A Rust server can instead embed the SDK in-process.

## PDF Notes example

For a PDF question-answering app, accept text-based PDFs, extract page-numbered
text and create one tool-free session per document. Reuse it for follow-up
questions and forward text events to the browser. Set upload/page/text limits,
run extraction with a timeout, and reject unsupported or malformed files. No OCR
or image understanding is implied. Model-generated page citations need separate
verification if the product promises citation accuracy.

Use the instructions and disabled optional capabilities in [sdk.md](sdk.md),
changing the agent's instructions to answer only from the supplied PDF text and
cite `[p. N]`. Rendering must treat model output as text or sanitized Markdown.
Do not put access tokens in URLs or expose an arbitrary harness request proxy.

For a shared instance, implement login, owner-bound document/session lookup,
quotas and retention. A shared app password plus random document handles is a
single-user demo, not multi-tenant authorization. Keep document expiry separate
from durable harness history retention.

## Container packaging

Install the matching `@openworkerai/harness-sdk` release inside a glibc-based Linux image,
with optional dependencies enabled, so npm selects the Linux runtime. Do not copy
macOS node_modules or its binary into that image. Supply runtime secrets and
writable state storage, handle SIGTERM by shutting down the harness, and expose
HTTPS through the platform's ingress. A managed child process needs a host that
supports its lifecycle; do not assume a request-limited function is a persistent
agent worker.

For Railway, default to one Node service serving the built frontend and HTTP API,
with the Rust harness as its managed child. Preserve an existing split if the app
already has one. Use [the deployment track](railway.md) and its bundled Docker assets.
Vercel recipes and operation of a separate Kubernetes harness service remain
separate work. For an existing service, consult https://openworker.com/docs/sdk/deployment/ and the
installed SDK's service API declarations before changing the application model.

## Reference source when useful

The maintained PDF Notes example is in `harness/examples/quickstart` of
https://github.com/andrewyng/openworker. Its `web/` files implement bounded PDF
extraction, HTTP routes and streaming. Reading the repository is optional; if
copying the example, use a matching revision and replace checkout dependencies.

Verify upload rejection, isolated document contexts, streamed responses,
follow-up questions, denied access and disconnect behavior. Use a synthetic model
endpoint for deterministic transport checks and label live-model qualification
separately.
