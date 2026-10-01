"""Editing MCP configs replaces fields safely and reloads just the selected server."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from coworker.mcp.config import edited_server_config, read_global
from coworker.server.app import create_app
from coworker.server.manager import SessionManager


@pytest.fixture
def manager(tmp_path):
    manager = SessionManager(data_dir=tmp_path / 'data')
    manager.add_mcp('example', {
        'command': 'old-server', 'args': ['old'],
        'env': {'TOKEN': 'secret', 'DROP': 'unused'},
    })
    manager.mcp.disconnect = AsyncMock()
    manager.mcp.ensure = AsyncMock(return_value=SimpleNamespace(tools=[SimpleNamespace(name='new-tool')]))
    return manager


async def test_replace_removes_old_fields_preserves_masks_and_reconnects(manager):
    result = await manager.replace_mcp('example', {
        'type': 'http', 'url': 'https://example.test/mcp', 'env': {'TOKEN': '***'},
    })
    assert result == {'ok': True, 'status': 'connected', 'tool_count': 1}
    assert read_global()['example'] == {
        'type': 'http', 'url': 'https://example.test/mcp', 'env': {'TOKEN': 'secret'},
    }
    manager.mcp.disconnect.assert_awaited_once_with('example')
    definition = manager.mcp.ensure.await_args.args[0]
    assert definition.command is None and definition.args == []
    assert definition.url == 'https://example.test/mcp'
    assert definition.env == {'TOKEN': 'secret'}
    assert 'secret' not in str(manager.list_mcp())


def test_header_masks_can_be_kept_replaced_or_removed():
    old = {'headers': {'Authorization': 'Bearer private', 'Drop': 'unused'}}
    kept = edited_server_config({'url': 'https://example.test/mcp', 'headers': {'Authorization': '***'}}, old)
    assert kept['headers'] == {'Authorization': 'Bearer private'}
    replaced = edited_server_config({'url': 'https://example.test/mcp', 'headers': {'Authorization': 'Bearer new'}}, old)
    assert replaced['headers']['Authorization'] == 'Bearer new'
    assert old['headers']['Authorization'] == 'Bearer private'


@pytest.mark.parametrize('config', [
    {}, {'url': 'not-a-url'}, {'command': 'echo', 'args': 'bad'},
    {'command': 'echo', 'enabled': 'false'}, {'command': 'echo', 'headers': []},
    {'command': 'echo', 'env': {'UNKNOWN': '***'}},
    {'command': 'echo', 'auth': 'oauth'}, {'command': 'echo', 'include_tools': [1]},
])
async def test_invalid_edit_never_saves_or_disconnects(manager, config):
    before = read_global()
    result = await manager.replace_mcp('example', config)
    assert result['ok'] is False and result['error']
    assert read_global() == before
    manager.mcp.disconnect.assert_not_awaited()
    manager.mcp.ensure.assert_not_awaited()


async def test_disabled_save_closes_without_connecting(manager):
    result = await manager.replace_mcp('example', {'command': 'new', 'enabled': False})
    assert result == {'ok': True, 'status': 'disabled'}
    manager.mcp.disconnect.assert_awaited_once_with('example')
    manager.mcp.ensure.assert_not_awaited()


async def test_connection_failure_is_saved_and_reported(manager):
    manager.mcp.ensure.side_effect = TimeoutError('timed out during initialize')
    result = await manager.replace_mcp('example', {'command': 'new-server'})
    assert result['ok'] is True and result['status'] == 'error'
    assert read_global()['example']['command'] == 'new-server'
    assert manager.list_mcp()[0]['last_error'] == 'timed out during initialize'
    assert not manager._mcp_reloading


async def test_oauth_url_change_forgets_registration_and_requires_signin(manager, monkeypatch):
    from coworker.mcp import oauth
    manager.add_mcp('example', {'url': 'https://old.test/mcp', 'auth': 'oauth'})
    signed_out = []
    monkeypatch.setattr(oauth, 'sign_out', lambda name, secrets: signed_out.append(name))
    monkeypatch.setattr(oauth, 'has_tokens', lambda *a: False)
    result = await manager.replace_mcp('example', {'url': 'https://new.test/mcp', 'auth': 'oauth'})
    assert result == {'ok': True, 'status': 'needs_auth'}
    assert signed_out == ['example']
    manager.mcp.ensure.assert_not_awaited()


async def test_same_oauth_url_reuses_login_without_opening_browser(manager, monkeypatch):
    from coworker.mcp import oauth
    manager.add_mcp('example', {'url': 'https://same.test/mcp', 'auth': 'oauth'})
    signout = AsyncMock()
    monkeypatch.setattr(oauth, 'sign_out', signout)
    monkeypatch.setattr(oauth, 'has_tokens', lambda *a: True)
    result = await manager.replace_mcp('example', {'url': 'https://same.test/mcp', 'auth': 'oauth', 'exclude_tools': ['write']})
    assert result['status'] == 'connected'
    signout.assert_not_called()
    assert manager.mcp.ensure.await_args.kwargs == {}  # non-interactive default


async def test_duplicate_save_is_rejected_while_reload_is_pending(manager):
    started, release = asyncio.Event(), asyncio.Event()
    async def disconnect(name):
        started.set()
        await release.wait()
    manager.mcp.disconnect.side_effect = disconnect
    first = asyncio.create_task(manager.replace_mcp('example', {'command': 'first'}))
    await started.wait()
    assert manager.list_mcp()[0]['status'] == 'reloading'
    second = await manager.replace_mcp('example', {'command': 'second'})
    assert second['ok'] is False
    release.set()
    assert (await first)['ok'] is True
    assert read_global()['example']['command'] == 'first'


def test_put_route_and_unknown_server(manager):
    client = TestClient(create_app(manager))
    response = client.put('/v1/mcp/example', json={'command': 'new', 'enabled': False})
    assert response.status_code == 200
    assert response.json() == {'ok': True, 'status': 'disabled'}
    assert client.put('/v1/mcp/missing', json={'command': 'new'}).json()['ok'] is False


async def test_edit_clears_stale_test_receipt_and_auth_hint(manager):
    manager._prefs['mcp_last_test'] = {'example': 123}
    manager._mcp_auth_hints.add('example')
    await manager.replace_mcp('example', {'command': 'new', 'enabled': False})
    row = manager.list_mcp()[0]
    assert row['last_test_at'] is None
    assert not row['auth_hint']


async def test_old_connect_failure_cannot_overwrite_saved_configuration(manager):
    started, release = asyncio.Event(), asyncio.Event()

    async def verify(*args, **kwargs):
        started.set()
        await release.wait()
        raise RuntimeError('old connection failed')

    manager.mcp.verify = verify
    old = asyncio.create_task(manager.connect_mcp('example'))
    await started.wait()
    saved = await manager.replace_mcp('example', {'command': 'new'})
    release.set()
    await old
    assert saved['status'] == 'connected'
    assert 'example' not in manager._mcp_errors
    assert 'example' not in manager._mcp_authorizing
