"""Matrix fake homeserver integration tests."""

from __future__ import annotations

import pytest

from coworker.testing.fake_matrix import DEFAULT_TOKEN, FakeMatrix


@pytest.mark.asyncio
async def test_fake_matrix_records_outbound_message():
    fake = FakeMatrix()
    await fake.start()
    try:
        import httpx

        async with httpx.AsyncClient(base_url=fake.base_url) as client:
            room = "!room:fake"
            resp = await client.put(
                f"/_matrix/client/v3/rooms/{room}/send/m.room.message/1",
                headers={"Authorization": f"Bearer {DEFAULT_TOKEN}"},
                json={"msgtype": "m.text", "body": "ping"},
            )
            assert resp.status_code == 200
            out = await client.get(f"{fake.base_url}/control/outbound")
            assert out.json()["messages"][0]["content"]["body"] == "ping"
    finally:
        await fake.stop()
