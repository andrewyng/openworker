"""Matrix media and lifecycle reaction unit tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from coworker.connectors.matrix_adapter import MatrixAdapter
from coworker.connectors.matrix_settings import MatrixSettings


def _adapter(**profile) -> MatrixAdapter:
    base = {"homeserver_url": "https://h", "access_token": "t"}
    base.update(profile)
    return MatrixAdapter(
        MatrixSettings.from_profile(base),
        store_path=Path("/tmp/matrix-media-test"),
    )


@pytest.mark.asyncio
async def test_lifecycle_reaction_on_dispatch_and_send():
    adapter = _adapter(lifecycle_reactions=True)
    adapter._client = MagicMock()
    adapter._client.room_send = AsyncMock(return_value=SimpleNamespace(event_id="$out1"))
    adapter._client.user_id = "@bot:ex"
    adapter.handle_message = AsyncMock()

    room = SimpleNamespace(room_id="!r:ex", display_name="ops")
    mapped = SimpleNamespace(
        message_id="$in1",
        agent_content=None,
        message_type=None,
        source=SimpleNamespace(thread_id=None),
    )

    if mapped.message_id and adapter.settings.lifecycle_reactions:
        await adapter._lifecycle_react(room.room_id, mapped.message_id, "👀")
        adapter._lifecycle_event[room.room_id] = mapped.message_id
    await adapter.handle_message(mapped)

    assert adapter._lifecycle_event["!r:ex"] == "$in1"
    assert adapter._client.room_send.await_count == 1

    await adapter.send("!r:ex", "reply")
    assert adapter._client.room_send.await_count == 3  # 👀 + text + ✅
    last_call = adapter._client.room_send.await_args_list[-1]
    assert last_call.args[1] == "m.reaction"
    assert last_call.args[2]["m.relates_to"]["key"] == "✅"


@pytest.mark.asyncio
async def test_media_agent_content_image():
    adapter = _adapter()
    adapter._client = MagicMock()
    adapter._client.user_id = "@bot:ex"
    adapter._client.download = AsyncMock(return_value=SimpleNamespace(body=b"\xff\xd8\xff"))

    event = SimpleNamespace(
        content={"url": "mxc://example.org/abc", "body": "photo.jpg"},
        sender="@alice:ex",
        body="photo.jpg",
        event_id="$img",
    )
    content = await adapter._media_agent_content("!r:ex", event)
    assert isinstance(content, list)
    assert content[0]["type"] == "text"
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/")


@pytest.mark.asyncio
async def test_send_file_bytes_uploads():
    adapter = _adapter()
    adapter._client = MagicMock()
    adapter._client.upload = AsyncMock(
        return_value=SimpleNamespace(
            content_uri="mxc://example.org/uploaded",
            parsed_body={"content_uri": "mxc://example.org/uploaded"},
        )
    )
    adapter._client.room_send = AsyncMock(return_value=SimpleNamespace(event_id="$f1"))
    result = await adapter.send_file_bytes(
        "!r:ex", b"pdf-bytes", "doc.pdf", thread_id=None, title="doc.pdf"
    )
    assert result.ok
    adapter._client.upload.assert_awaited_once()
    adapter._client.room_send.assert_awaited_once()
