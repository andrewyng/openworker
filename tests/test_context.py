from __future__ import annotations

import copy
import hashlib
import json
import math

from coworker.context import ContextBudget, ContextProjection


def test_budget_defaults_preserve_direct_provider_attachment_limits():
    budget = ContextBudget()

    assert budget.soft_message_limit == 220
    assert budget.hard_message_limit == 256
    assert budget.soft_request_bytes is None
    assert budget.hard_request_bytes is None
    assert budget.historical_tool_argument_redaction_bytes == 8_192


def test_gateway_budget_reserves_wire_adapter_headroom_below_two_mebibytes():
    budget = ContextBudget.gateway()

    assert budget.soft_request_bytes == 1_572_864
    assert budget.hard_request_bytes == 2_031_616
    assert budget.hard_request_bytes < 2 * 1_024 * 1_024


def test_projection_reports_exact_compact_utf8_request_size():
    projection = ContextProjection.build(
        [{"role": "user", "content": "é"}],
        budget=ContextBudget(),
        model="m",
        tools=[{"type": "function"}],
        settings={"temperature": 0},
    )
    expected_body = (
        b'{"model":"m","messages":[{"role":"user","content":"'
        + "é".encode("utf-8")
        + b'"}],"tools":[{"type":"function"}],"temperature":0}'
    )

    assert projection.request_bytes == len(expected_body)
    assert projection.estimated_tokens == math.ceil(len(expected_body) / 4)


def test_projection_bounds_257_messages_and_keeps_latest_user():
    messages = [{"role": "system", "content": "instructions"}]
    messages.extend(
        {"role": "user", "content": f"message {index}"} for index in range(255)
    )
    messages.append({"role": "user", "content": "latest user turn"})

    budget = ContextBudget()
    projection = ContextProjection.build(
        messages,
        budget=budget,
        model="test-model",
        tools=[],
        settings={},
    )

    assert projection.message_count <= 220
    assert projection.message_count == len(projection.messages)
    assert projection.messages[0] == {
        "role": "system",
        "content": "instructions",
    }
    assert projection.messages[-1] == {
        "role": "user",
        "content": "latest user turn",
    }
    assert (
        sum(
            "Earlier conversation omitted" in message.get("content", "")
            for message in projection.messages
        )
        == 1
    )


def test_projection_protects_the_entire_leading_system_prefix():
    messages = [
        {"role": "system", "content": "base instructions"},
        {"role": "system", "content": "policy instructions"},
        {"role": "user", "content": "old turn"},
        {"role": "user", "content": "latest turn"},
    ]

    projection = ContextProjection.build(
        messages,
        budget=ContextBudget(
            soft_message_limit=3,
            hard_message_limit=4,
        ),
        model="test-model",
    )

    assert projection.messages[:2] == messages[:2]
    assert projection.messages[-1] == messages[-1]
    assert all(message.get("content") != "old turn" for message in projection.messages)


def test_projection_uses_dynamic_token_target_from_model_context_window():
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "o" * 160_000},
        {"role": "user", "content": "l" * 160_000},
    ]

    budget = ContextBudget()
    projection = ContextProjection.build(
        messages,
        budget=budget,
        model="test-model",
        model_context_window=100_000,
        tools=[],
        settings={},
    )

    assert projection.soft_token_limit == 80_000
    assert projection.hard_token_limit == 100_000
    assert projection.estimated_tokens <= 80_000
    assert projection.messages[-1]["content"] == "l" * 160_000
    assert all(
        message.get("content") != "o" * 160_000 for message in projection.messages
    )


def test_projection_bounds_four_625kb_tool_results():
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "latest user turn"},
    ]
    for index in range(4):
        call_id = f"call-{index}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "crm_read",
                                "arguments": '{"query":"accounts"}',
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": "x" * 625_000,
                },
            ]
        )

    projection = ContextProjection.build(
        messages,
        budget=ContextBudget.gateway(),
        model="test-model",
        tools=[],
        settings={},
    )

    assert projection.request_bytes <= 1_572_864
    assert projection.request_bytes < 2 * 1_024 * 1_024
    assert projection.omitted_message_count > 0
    assert projection.messages[1]["role"] == "system"
    assert "Earlier conversation omitted" in projection.messages[1]["content"]
    assert {"role": "user", "content": "latest user turn"} in projection.messages


def test_projection_never_orphans_an_assistant_tool_call_or_its_results():
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "older turn"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-a",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                },
                {
                    "id": "call-b",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call-a", "content": "a"},
        {"role": "tool", "tool_call_id": "call-b", "content": "b"},
        {"role": "user", "content": "latest user turn"},
    ]

    projection = ContextProjection.build(
        messages,
        budget=ContextBudget(
            soft_message_limit=5,
            hard_message_limit=6,
            soft_request_bytes=10_000,
            hard_request_bytes=20_000,
        ),
        model="test-model",
        tools=[],
        settings={},
    )

    call_ids = {
        call["id"]
        for message in projection.messages
        for call in message.get("tool_calls", [])
    }
    result_ids = {
        message["tool_call_id"]
        for message in projection.messages
        if message.get("role") == "tool"
    }
    assert call_ids == result_ids
    assert projection.omitted_group_count == 1


def test_projection_drops_historical_user_turns_without_leaving_their_answer():
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "old prompt " + ("x" * 20_000)},
        {"role": "assistant", "content": "answer that depends on the old prompt"},
        {"role": "user", "content": "latest user turn"},
    ]

    projection = ContextProjection.build(
        messages,
        budget=ContextBudget(
            soft_request_bytes=1_000,
            hard_request_bytes=100_000,
        ),
        model="test-model",
        tools=[],
        settings={},
    )

    assert all(
        "old prompt" not in str(message.get("content"))
        and "answer that depends" not in str(message.get("content"))
        for message in projection.messages
    )
    assert projection.messages[-1]["content"] == "latest user turn"


def test_projection_strips_display_state_without_mutating_durable_history():
    messages = [
        {
            "role": "system",
            "content": "instructions",
            "ts": 1.0,
            "usage": {"input": 10},
        },
        {
            "role": "notice",
            "kind": "model_switch",
            "text": "Model switched",
            "ts": 2.0,
        },
        {
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
            "source": {"connector": "slack"},
            "_display": {"card": True},
            "reasoning": "display-only thought",
            "ts": 3.0,
        },
    ]
    durable_snapshot = copy.deepcopy(messages)

    projection = ContextProjection.build(
        messages,
        budget=ContextBudget(),
        model="test-model",
        tools=[],
        settings={},
    )

    assert messages == durable_snapshot
    assert [message["role"] for message in projection.messages] == ["system", "user"]
    assert all(
        sidecar not in message
        for message in projection.messages
        for sidecar in ("source", "_display", "ts", "reasoning", "usage")
    )

    projection.messages[-1]["content"][0]["text"] = "provider mutation"
    assert messages[-1]["content"][0]["text"] == "hello"


def test_projection_replaces_completed_write_file_content_with_bounded_metadata():
    artifact_content = "résumé\n" * 20_000
    messages = [
        {"role": "system", "content": "instructions"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "write-1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {
                                "path": "artifacts/report.md",
                                "content": artifact_content,
                            }
                        ),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "write-1", "content": "report.md"},
        {"role": "user", "content": "latest user turn"},
    ]
    durable_snapshot = copy.deepcopy(messages)

    projection = ContextProjection.build(
        messages,
        budget=ContextBudget(),
        model="test-model",
        tools=[],
        settings={},
    )

    assistant = next(
        message for message in projection.messages if message["role"] == "assistant"
    )
    projected_arguments = json.loads(
        assistant["tool_calls"][0]["function"]["arguments"]
    )
    artifact_bytes = artifact_content.encode("utf-8")

    assert projected_arguments["path"] == "artifacts/report.md"
    assert "content" not in projected_arguments
    assert projected_arguments["content_bytes"] == len(artifact_bytes)
    assert (
        projected_arguments["content_sha256"]
        == hashlib.sha256(artifact_bytes).hexdigest()
    )
    assert "read_file" in projected_arguments["context_note"]
    assert artifact_content not in assistant["tool_calls"][0]["function"]["arguments"]
    assert projection.redacted_tool_argument_bytes == len(artifact_bytes)
    assert messages == durable_snapshot


def test_projection_keeps_trailing_write_exchange_for_immediate_follow_up():
    artifact_content = "x" * 20_000
    messages = [
        {"role": "system", "content": "instructions"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "write-current",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"path": "current.md", "content": artifact_content}
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "write-current",
            "content": "current.md",
        },
    ]

    projection = ContextProjection.build(
        messages,
        budget=ContextBudget(),
        model="test-model",
        tools=[],
        settings={},
    )

    assistant = projection.messages[-2]
    projected_arguments = json.loads(
        assistant["tool_calls"][0]["function"]["arguments"]
    )
    assert projected_arguments["content"] == artifact_content
    assert projection.redacted_tool_argument_bytes == 0


def test_projection_never_drops_the_current_trailing_tool_exchange():
    artifact_content = "x" * 1_600_000
    budget = ContextBudget.gateway()
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "write the artifact"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "write-current",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"path": "current.md", "content": artifact_content}
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "write-current",
            "content": "current.md",
        },
    ]

    projection = ContextProjection.build(
        messages,
        budget=budget,
        model="test-model",
        tools=[],
        settings={},
    )

    assert any(
        call.get("id") == "write-current"
        for message in projection.messages
        for call in message.get("tool_calls", [])
    )
    assert any(
        message.get("tool_call_id") == "write-current"
        for message in projection.messages
    )
    assert (
        budget.soft_request_bytes
        < projection.request_bytes
        <= budget.hard_request_bytes
    )


def test_projection_preserves_current_tool_exchange_when_steering_follows_results():
    budget = ContextBudget.gateway()
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "look up the full account"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "lookup-current",
                    "type": "function",
                    "function": {
                        "name": "crm_lookup",
                        "arguments": '{"account":"Apollo"}',
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "lookup-current",
            "content": "x" * 1_600_000,
        },
        {
            "role": "user",
            "content": "focus on the supply-chain team",
            "_steering": True,
        },
    ]

    projection = ContextProjection.build(
        messages,
        budget=budget,
        model="test-model",
        tools=[],
        settings={},
    )

    assert projection.messages == [
        *messages[:-1],
        {"role": "user", "content": "focus on the supply-chain team"},
    ]
    assert projection.omitted_message_count == 0
    assert (
        budget.soft_request_bytes
        < projection.request_bytes
        <= budget.hard_request_bytes
    )


def test_projection_keeps_just_completed_write_arguments_exact_before_steering():
    artifact_content = "x" * 20_000
    messages = [
        {"role": "system", "content": "instructions"},
        {"role": "user", "content": "write the artifact"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "write-current",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"path": "current.md", "content": artifact_content}
                        ),
                    },
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "write-current",
            "content": "current.md",
        },
        {"role": "user", "content": "also add a heading", "_steering": True},
    ]

    projection = ContextProjection.build(
        messages,
        budget=ContextBudget(),
        model="test-model",
        tools=[],
        settings={},
    )

    assistant = next(
        message for message in projection.messages if message.get("tool_calls")
    )
    projected_arguments = json.loads(
        assistant["tool_calls"][0]["function"]["arguments"]
    )
    assert projected_arguments["content"] == artifact_content
    assert projection.redacted_tool_argument_bytes == 0


def test_projection_does_not_rewrite_signed_historical_tool_arguments():
    artifact_content = "x" * 20_000
    arguments = json.dumps({"path": "signed.md", "content": artifact_content})
    signed_sidecar = {"text_sig": "", "call_sigs": ["c2lnbmF0dXJl"]}
    messages = [
        {
            "role": "assistant",
            "_gemini": signed_sidecar,
            "tool_calls": [
                {
                    "id": "signed-write",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": arguments,
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "signed-write", "content": "signed.md"},
        {"role": "user", "content": "later turn"},
    ]

    projection = ContextProjection.build(
        messages,
        budget=ContextBudget(),
        model="gemini",
        replay_sidecar_keys={"_gemini"},
    )

    assistant = next(
        message for message in projection.messages if message.get("tool_calls")
    )
    assert assistant["_gemini"] == signed_sidecar
    assert assistant["tool_calls"][0]["function"]["arguments"] == arguments
    assert projection.redacted_tool_argument_bytes == 0


def test_direct_provider_token_estimate_does_not_treat_image_base64_as_text():
    image_data = "data:image/png;base64," + ("A" * 3_000_000)
    projection = ContextProjection.build(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "inspect this"},
                    {"type": "image_url", "image_url": {"url": image_data}},
                ],
            }
        ],
        budget=ContextBudget(),
        model="vision-model",
        model_context_window=400_000,
    )

    assert projection.request_bytes > 3_000_000
    assert projection.estimated_tokens < 40_000
    assert projection.messages[0]["content"][1]["image_url"]["url"] == image_data


def test_projection_keeps_small_historical_write_arguments_inline():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "write-small",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {"path": "small.md", "content": "small"}
                        ),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "write-small", "content": "small.md"},
        {"role": "user", "content": "later turn"},
    ]

    projection = ContextProjection.build(
        messages,
        budget=ContextBudget(),
        model="test-model",
        tools=[],
        settings={},
    )

    assistant = projection.messages[0]
    projected_arguments = json.loads(
        assistant["tool_calls"][0]["function"]["arguments"]
    )
    assert projected_arguments["content"] == "small"
    assert projection.redacted_tool_argument_bytes == 0


def test_projection_bounds_historical_replace_in_file_old_and_new_text():
    old_text = "old\n" * 3_000
    new_text = "new\n" * 4_000
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "replace-1",
                    "type": "function",
                    "function": {
                        "name": "replace_in_file",
                        "arguments": json.dumps(
                            {
                                "path": "large.py",
                                "old": old_text,
                                "new": new_text,
                                "expected_replacements": 1,
                            }
                        ),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "replace-1", "content": '{"ok":true}'},
        {"role": "user", "content": "later turn"},
    ]

    projection = ContextProjection.build(
        messages,
        budget=ContextBudget(),
        model="test-model",
        tools=[],
        settings={},
    )

    assistant = projection.messages[0]
    projected_arguments = json.loads(
        assistant["tool_calls"][0]["function"]["arguments"]
    )
    assert projected_arguments["path"] == "large.py"
    assert projected_arguments["expected_replacements"] == 1
    assert "old" not in projected_arguments
    assert "new" not in projected_arguments
    assert projected_arguments["old_bytes"] == len(old_text.encode("utf-8"))
    assert projected_arguments["new_bytes"] == len(new_text.encode("utf-8"))
    assert len(projected_arguments["old_sha256"]) == 64
    assert len(projected_arguments["new_sha256"]) == 64
    assert projection.redacted_tool_argument_bytes == len(
        (old_text + new_text).encode("utf-8")
    )


def test_projection_drops_incomplete_tool_groups_and_orphan_results():
    messages = [
        {"role": "system", "content": "instructions"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-a",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                },
                {
                    "id": "call-b",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call-a", "content": "partial"},
        {"role": "tool", "tool_call_id": "orphan", "content": "orphan"},
        {"role": "user", "content": "latest user turn"},
    ]

    projection = ContextProjection.build(
        messages,
        budget=ContextBudget(),
        model="test-model",
        tools=[],
        settings={},
    )

    assert [message["role"] for message in projection.messages] == [
        "system",
        "system",
        "user",
    ]
    assert "Earlier conversation omitted" in projection.messages[1]["content"]
    assert projection.omitted_message_count == 3
    assert projection.omitted_group_count == 1
