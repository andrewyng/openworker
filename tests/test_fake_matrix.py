"""Fake Matrix homeserver harness tests."""

from __future__ import annotations

import httpx
import pytest

from coworker.testing.fake_matrix import DEFAULT_TOKEN, FakeMatrix


@pytest.mark.asyncio
async def test_fake_matrix_whoami_and_send():
    fake = FakeMatrix()
    await fake.start()
    try:
        async with httpx.AsyncClient(base_url=fake.base_url) as client:
            w = await client.get(
                "/_matrix/client/v3/account/whoami",
                headers={"Authorization": f"Bearer {DEFAULT_TOKEN}"},
            )
            assert w.status_code == 200
            assert w.json()["user_id"] == fake.user_id
            room = "!test:fake.local"
            txn = "1"
            send = await client.put(
                f"/_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}",
                headers={"Authorization": f"Bearer {DEFAULT_TOKEN}"},
                json={"msgtype": "m.text", "body": "hello"},
            )
            assert send.status_code == 200
            out = await client.get("/control/outbound")
            assert len(out.json()["messages"]) == 1
            assert out.json()["messages"][0]["content"]["body"] == "hello"
    finally:
        await fake.stop()
