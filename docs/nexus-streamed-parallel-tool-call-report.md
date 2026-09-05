# Nexus report: missing tool-call metadata in streamed parallel calls

Date observed: 2026-08-13  
Affected endpoint: `https://nexus-api.dappnode.com/v1/chat/completions`  
Confirmed affected route: `private/glm-5.2`  
Client: OpenAI Python SDK `2.53.0`, Chat Completions API

## Summary

In two independent OpenWorker sessions, an OpenAI-compatible client observed an
incomplete tool call while consuming a streamed parallel tool-call batch from Nexus route
`private/glm-5.2`. The affected call contained valid JSON arguments, but its `id` and
`function.name` were both absent after the stream was accumulated.

OpenWorker then produced a `role: "tool"` result with an empty `tool_call_id`. On the next
request, Nexus correctly rejected that invalid history:

```text
Error code: 400 - {'error': {'type': 'invalid_request_error',
'code': 'tool_message_invalid',
'message': 'message[15]: tool message requires tool_call_id'}}
```

The same behavior produced `message[23]` in the first session. The message index differs
only because the conversations had different lengths.

Controlled probes indicate that this is specific to streamed parallel tool-call handling:

- Non-streaming `private/glm-5.2` returned a non-empty ID and function name for every call.
- Streaming with `parallel_tool_calls=false` returned one complete call with a non-empty ID
  and function name.
- Streaming with parallel calls enabled produced the incomplete-call behavior during real
  agent tasks.

This points to the Nexus streaming/router layer or the GLM serving adapter behind Nexus.
It does not yet prove which component drops the metadata. We did not retain the raw SSE
frames from the original incidents, so a raw-wire comparison is an important next step.

## Observed incidents

No prompts, file contents, tool outputs, API keys, or other user data are included below.

### Incident 1

- Model: `private/glm-5.2`
- Approximate failure time: 2026-08-13 10:59 ICT (03:59 UTC)
- Assistant requested two parallel tools.
- Call 0 was complete:
  - `id`: present
  - `function.name`: `run_shell`
  - `function.arguments`: present
- Call 1 was incomplete after stream accumulation:
  - `id`: empty
  - `function.name`: empty
  - `function.arguments`: present and valid JSON
- The subsequent request failed with:
  - `code`: `tool_message_invalid`
  - `message`: `message[23]: tool message requires tool_call_id`

### Incident 2

- Model: `private/glm-5.2`
- Approximate failure time: 2026-08-13 11:53 ICT (04:53 UTC)
- Several earlier single and parallel tool rounds succeeded.
- The affected assistant turn requested five parallel calls.
- Four calls were complete: one `todo_write` and three `read_file` calls.
- The call in tool slot/index 1 contained valid `{"path": ...}` arguments but had:
  - `id`: empty
  - `function.name`: empty
- The subsequent request failed with:
  - `code`: `tool_message_invalid`
  - `message`: `message[15]: tool message requires tool_call_id`

The incomplete entry was not a duplicate of another call: it referenced a different file
from the three valid `read_file` calls. Its arguments strongly suggest it was intended to
be another `read_file` call whose metadata was lost.

## Expected behavior

For every completed function call in `choices[0].delta.tool_calls`, the accumulated call
must have:

```json
{
  "index": 1,
  "id": "call_non_empty",
  "type": "function",
  "function": {
    "name": "probe_beta",
    "arguments": "{\"value\":\"probe_beta\"}"
  }
}
```

Continuation chunks may omit fields already supplied by an earlier chunk. However, for
each tool-call index, at least one chunk must supply the call ID and function name so the
client can reconstruct a valid call and correlate its result.

## Actual behavior

For one call within a parallel batch, the accumulated representation was equivalent to:

```json
{
  "index": 1,
  "id": "",
  "type": "function",
  "function": {
    "name": "",
    "arguments": "{\"value\":\"probe_beta\"}"
  }
}
```

The arguments were emitted, but no chunk observed by the client supplied the ID or name
for that index.

## Minimal reproduction

This synthetic probe uses no user or project data. It reads the API key from the
`NEXUS_API_KEY` environment variable and prints only tool-call metadata.

Because the issue may be intermittent, run the streaming-parallel probe several times and
inspect every accumulated slot. The probe intentionally uses five independent tools to
exercise the same pattern as the second incident.

```python
import json
import os

from openai import OpenAI


client = OpenAI(
    api_key=os.environ["NEXUS_API_KEY"],
    base_url="https://nexus-api.dappnode.com/v1",
)

names = [
    "probe_alpha",
    "probe_beta",
    "probe_gamma",
    "probe_delta",
    "probe_epsilon",
]
tools = [
    {
        "type": "function",
        "function": {
            "name": name,
            "description": "Independent synthetic metadata probe",
            "parameters": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        },
    }
    for name in names
]

stream = client.chat.completions.create(
    model="private/glm-5.2",
    messages=[
        {
            "role": "user",
            "content": (
                "Call all five tools exactly once with value set to the tool name. "
                "The calls are independent and may be parallel. Do not answer in prose."
            ),
        }
    ],
    tools=tools,
    tool_choice="required",
    parallel_tool_calls=True,
    stream=True,
    stream_options={"include_usage": True},
    max_tokens=512,
)

accumulated = {}
finish_reason = None


def valid_json(value):
    try:
        json.loads(value)
        return True
    except (TypeError, json.JSONDecodeError):
        return False

for chunk in stream:
    if not chunk.choices:
        continue
    choice = chunk.choices[0]
    finish_reason = choice.finish_reason or finish_reason
    for call in choice.delta.tool_calls or []:
        slot = accumulated.setdefault(
            call.index,
            {"id": "", "name": "", "arguments": ""},
        )
        if call.id:
            slot["id"] = call.id
        if call.function:
            if call.function.name:
                slot["name"] = call.function.name
            if call.function.arguments:
                slot["arguments"] += call.function.arguments

result = {
    "finish_reason": finish_reason,
    "calls": [
        {
            "index": index,
            "id_present": bool(call["id"]),
            "name": call["name"],
            "arguments_valid_json": valid_json(call["arguments"]),
        }
        for index, call in sorted(accumulated.items())
    ],
}
print(json.dumps(result, indent=2))
```

A simpler validation condition for each accumulated slot is:

```python
assert call["id"], f"missing id at tool-call index {index}"
assert call["name"], f"missing function name at tool-call index {index}"
```

## Comparison probes

Please compare the raw server output for these three modes using the same model, prompt,
and tool definitions:

1. `stream=true`, `parallel_tool_calls=true` — affected mode.
2. `stream=true`, `parallel_tool_calls=false` — locally returned a complete call.
3. `stream=false`, `parallel_tool_calls=true` — locally returned IDs and names for all
   calls, although the forced synthetic prompt also caused repeated calls to the final
   tool until `finish_reason=length`; that repetition may be a separate model behavior.

## Suggested Nexus investigation

For a failing request, inspect raw SSE before and after each transformation layer:

1. The private GLM inference server's native tool-call output.
2. The model-specific tool parser/template adapter.
3. Nexus's normalization into OpenAI Chat Completions deltas.
4. The final SSE frames sent to the client.

For every `tool_calls[*].index`, verify that an initial delta containing `id` and
`function.name` is emitted before argument-only continuation deltas. Particular things to
check:

- Whether the initial delta for index 1 is being dropped or overwritten.
- Whether parallel entries are incorrectly sharing parser state.
- Whether list position is being confused with the `index` value.
- Whether an empty placeholder entry is emitted while arguments are attached to it later.
- Whether the private route and public GLM route use different chat templates or stream
  normalizers.
- Whether proxy buffering or SSE transformation discards a metadata-only frame whose
  `content` field is empty.

Correlating a failing client timestamp/request ID with Nexus server logs would establish
whether the malformed call originates in the model server or is introduced by the Nexus
gateway. We did not capture or retain Nexus request IDs in the original incidents.

## OpenWorker mitigation

OpenWorker now has a locally implemented and verified mitigation:

- Nexus requests set `parallel_tool_calls=false` while retaining streaming.
- Every fully accumulated call is validated before execution or persistence.
- Missing/duplicate IDs or missing names stop the turn instead of poisoning history.
- Previously saved malformed calls are removed only from the outbound provider copy; the
  canonical transcript remains unchanged for audit purposes.

This keeps private Nexus models usable, but it serializes tool selection into multiple
model iterations. A Nexus-side fix would allow parallel calls to be safely re-enabled.

## Impact

- Agent tasks fail after an otherwise successful tool round.
- Retry cannot recover unless malformed history is repaired or a new task is started.
- Switching models does not help because the invalid tool result remains in shared history.
- Any OpenAI-compatible client that trusts the streamed response can create invalid
  follow-up history, so the issue is not specific to OpenWorker.

## Requested response from Nexus

Please confirm:

1. Whether Nexus can reproduce missing `id`/`function.name` fields for a completed tool
   call in a streamed parallel batch.
2. Whether the malformed representation is already present in the private GLM server's
   native output or is introduced by Nexus normalization.
3. Whether `parallel_tool_calls=false` is the recommended temporary compatibility mode.
4. Whether other private Nexus routes share the same streaming/tool parser.
5. When it is safe for clients to re-enable parallel tool calls.
