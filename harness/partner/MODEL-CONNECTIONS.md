# Model connections — development protocol extension

These are trusted app-to-runtime requests, never model tools. Gate on initialize capabilities
`model_connections` and `secure_model_credentials`. Existing environment-key
integrations keep working when no managed selection/default is configured.

## Configure a connection

Send through the private stdio pipe, never command-line arguments or telemetry:

```json
{"jsonrpc":"2.0","id":1,"method":"connection/set","params":{"id":"personal","label":"My OpenAI account","provider":"openai","api_key":"KEY_FROM_USER_INPUT","persistence":"secure"}}
```

`openai` uses the fixed Responses endpoint; `anthropic` uses the fixed Claude
Messages endpoint. Initialize advertises `model_providers`. A connection's provider
cannot be changed in place: use a new connection ID. Arbitrary endpoints,
other API formats, organization/project headers and OAuth remain separate work.
Redirects and ambient proxies are disabled for model requests; no key can be
silently redirected to another endpoint. Model IDs are validated syntactically;
provider availability/access is checked by the actual request, not a claimed
prevalidated model catalog. Unsupported/unavailable models fail without fallback.

- `secure`: Windows current-user DPAPI encrypted blob, separate
  `model-connections.sqlite` under the private runtime state directory. Scope is
  bound to that canonical state directory and connection ID using DPAPI entropy.
  UI is forbidden in DPAPI calls; no machine-wide encryption flag. Protected DACL.
- `memory`: only nonsecret metadata survives restart. The app must resupply the key.
- Other operating systems reject secure persistence, without plaintext fallback.
  A supported headless-Linux secret store remains follow-up work.
- Same-user/in-process hostile code is outside this storage protection boundary.
  Moving/copying this credential database is not a supported credential export.
- At-rest secrets do not enter session/event databases, bundles, prompts or MCP
  environments. The trusted UI/backend necessarily handles the key transiently
  while forwarding user input; responses never return it.

`connection/list {}` returns `connections` containing ID, label, provider,
persistence and `configured`, plus `secure_storage_available`. Configured means
credential material is present, not that an API call validated account access.
`connection/remove {"id":"personal"}` forgets the stored key; it does not revoke
the provider's key or roll back an already-started request.

Updating a connection preserves its identity and applies to subsequent requests.
Deleting and recreating an ID creates a new identity; old sessions cannot silently
switch accounts through ID reuse. Do not automatically retry uncertain mutations.
Connection count/account administration is operator-owned, not agent-controlled.

## Defaults and session selection

```json
{"jsonrpc":"2.0","id":2,"method":"model/defaults/set","params":{"agent":{"connection":"personal","model":"gpt-6-sol"},"reviewer":{"connection":"personal","model":"gpt-6-astra"}}}
```

`model/defaults/get {}` reads the nonsecret defaults. Either value may be null.
The reviewer is independent: changing the working agent's model never changes it.
For a managed session with no reviewer configured, Auto-approve asks the human;
it does not reuse the working agent's model implicitly.

The coworker manifest may optionally contain:

```yaml
default_model:
  connection: personal
  model: gpt-6-sol
```

`session/create` may supply `model: {connection, model}` and separately
`reviewer_model: {connection, model}`. Agent precedence is session override,
coworker default, app default, then the legacy process model/environment path when
none of those managed choices exists. A missing configured choice does NOT fall
back. Reviewer selection is explicit session override or independent app default;
legacy sessions retain their pre-existing reviewer behavior.

Resolved provider, model, connection and connection identity are saved with the session.
Changing defaults or the CLI model does not alter a managed session on reconnect.
Missing/rejected keys cause an explicit configuration/provider error, never a
provider substitution or blind tool replay. Restore the key before starting more
work; there is no automatic resume-after-credential-change operation in this slice.

## References

- [OpenAI API authentication](https://developers.openai.com/api/reference/overview#authentication)
- [OpenAI streaming responses](https://developers.openai.com/api/docs/guides/streaming-responses)
- [Microsoft CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata)

The [Rust SDK](../harness-sdk/README.md) exposes these methods through its request
API in embedded and managed-stdio modes; this remains a development interface.
For multi-node Linux deployments, use the host-owned identity references and
worker-only credentials in [Models and credentials](../../docs/harness/guides/providers.md).
