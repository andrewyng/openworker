"""Regression coverage for stalled discovery, shared connections and large catalogs."""
import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import anyio
import pytest

from coworker.mcp.client import MCPManager
from coworker.mcp.config import MCPServerDef


@pytest.fixture
def transport(monkeypatch):
    sessions = {}
    entered, exited = [], []

    @asynccontextmanager
    async def stdio(params, **kwargs):
        owner = asyncio.current_task()
        entered.append(params.command)
        # Like the real SDK, this scope must be exited by its entering task.
        with anyio.CancelScope():
            try:
                yield sessions[params.command], None
            finally:
                assert asyncio.current_task() is owner
                exited.append(params.command)

    @asynccontextmanager
    async def session(read, write):
        yield read

    monkeypatch.setattr('coworker.mcp.client.stdio_client', stdio)
    monkeypatch.setattr('coworker.mcp.client.ClientSession', session)
    return sessions, entered, exited


class Session:
    def __init__(self, *, hang=None, pages=None):
        self.hang = hang
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.pages = pages or {None: (['tool'], None)}
        self.cursors = []

    async def initialize(self):
        self.started.set()
        if self.hang == 'initialize':
            await self.release.wait()

    async def list_tools(self, cursor=None):
        self.cursors.append(cursor)
        if self.hang == 'tools/list':
            await self.release.wait()
        names, next_cursor = self.pages[cursor]
        return SimpleNamespace(tools=[SimpleNamespace(name=n) for n in names], nextCursor=next_cursor)


def server(name):
    return MCPServerDef(name=name, transport='stdio', command=name)


@pytest.mark.parametrize('stage', ['initialize', 'tools/list'])
async def test_stalled_server_times_out_without_blocking_healthy_server(transport, stage):
    sessions, entered, exited = transport
    sessions.update(slow=Session(hang=stage), healthy=Session())
    manager = MCPManager(connect_timeout=.15)
    slow = asyncio.create_task(manager.ensure(server('slow')))
    try:
        await sessions['slow'].started.wait()
        conn = await asyncio.wait_for(manager.ensure(server('healthy')), .1)
        assert conn.tools[0].name == 'tool'
        with pytest.raises(TimeoutError, match=stage):
            await slow
        await asyncio.sleep(.01)
        assert 'slow' not in manager._tasks
        assert 'slow' in exited
        assert 'healthy' in manager._conns
        # A corrected server can be retried.
        sessions['slow'].release.set()
        await manager.ensure(server('slow'))
    finally:
        await manager.aclose()
    assert sorted(entered) == sorted(exited)


async def test_same_server_shared_and_cancelled_waiter_does_not_poison_connection(transport):
    sessions, entered, exited = transport
    sessions['shared'] = Session(hang='initialize')
    manager = MCPManager()
    first = asyncio.create_task(manager.ensure(server('shared')))
    second = asyncio.create_task(manager.ensure(server('shared')))
    try:
        await sessions['shared'].started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        sessions['shared'].release.set()
        conn = await second
        assert await manager.ensure(server('shared')) is conn
        assert entered == ['shared']
    finally:
        await manager.aclose()
    assert exited == ['shared']


async def test_close_pending_attempt_unblocks_waiter_and_cleans_owner(transport):
    sessions, entered, exited = transport
    sessions['slow'] = Session(hang='initialize')
    manager = MCPManager()
    waiter = asyncio.create_task(manager.ensure(server('slow')))
    await sessions['slow'].started.wait()
    await asyncio.wait_for(manager.aclose(), .5)
    with pytest.raises(RuntimeError, match='cancelled'):
        await waiter
    assert not manager._tasks and not manager._ready and not manager._conns
    assert entered == exited == ['slow']


async def test_session_does_not_wait_for_interactive_login(transport):
    sessions, _, _ = transport
    sessions['oauth'] = Session(hang='initialize')
    manager = MCPManager(connect_timeout=.01, interactive_timeout=1)
    login = asyncio.create_task(manager.ensure(server('oauth'), interactive=True))
    try:
        await sessions['oauth'].started.wait()
        with pytest.raises(RuntimeError, match='sign-in in progress'):
            await manager.ensure(server('oauth'))
        await asyncio.sleep(.02)
        assert not login.done()
        sessions['oauth'].release.set()
        await login
    finally:
        await manager.aclose()


async def test_reads_all_500_tools_across_pages(transport):
    sessions, _, _ = transport
    sessions['large'] = Session(pages={
        None: ([f'tool_{n}' for n in range(250)], 'page2'),
        'page2': ([f'tool_{n}' for n in range(250, 500)], None),
    })
    manager = MCPManager()
    try:
        conn = await manager.ensure(server('large'))
        assert [t.name for t in conn.tools] == [f'tool_{n}' for n in range(500)]
        assert sessions['large'].cursors == [None, 'page2']
    finally:
        await manager.aclose()


async def test_repeating_cursor_fails_instead_of_looping(transport):
    sessions, _, _ = transport
    sessions['broken'] = Session(pages={None: ([], 'again'), 'again': ([], 'again')})
    manager = MCPManager()
    try:
        with pytest.raises(Exception, match='invalid tools pagination'):
            await manager.ensure(server('broken'))
        assert not manager._conns
    finally:
        await manager.aclose()


async def test_session_prepares_servers_concurrently_and_records_errors(tmp_path, monkeypatch):
    from coworker.server.manager import SessionManager
    monkeypatch.setenv('COWORKER_STATE_DIR', str(tmp_path / 'state'))
    manager = SessionManager(data_dir=tmp_path / 'data')
    monkeypatch.setattr('coworker.server.manager.load_mcp_servers', lambda *a, **k: [server('slow'), server('healthy')])
    healthy_started = asyncio.Event()

    async def ensure(definition):
        if definition.name == 'slow':
            await asyncio.wait_for(healthy_started.wait(), .2)
            raise TimeoutError('MCP slow timed out during initialize')
        healthy_started.set()
        return SimpleNamespace(tools=[SimpleNamespace(name='ok', description='ok', inputSchema={})])

    monkeypatch.setattr(manager.mcp, 'ensure', ensure)
    tools = await manager.prepare_mcp_tools('new-session', workspace=str(tmp_path))
    assert [tool.__name__ for tool in tools] == ['mcp__healthy__ok']
    assert 'timed out during initialize' in manager._mcp_errors['slow']


@pytest.fixture
def stdio_server(tmp_path):
    import sys
    script = tmp_path / 'server.py'
    script.write_text('''import json, os, sys
from pathlib import Path
Path(sys.argv[2]).write_text(str(os.getpid()))
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    if 'id' not in request:
        continue
    if sys.argv[1] == 'stall':
        continue
    if method == 'initialize':
        result = {'protocolVersion': request['params']['protocolVersion'],
                  'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fixture', 'version': '1'}}
    elif method == 'tools/list':
        start = 300 if request.get('params', {}).get('cursor') else 0
        stop = 500 if start else 300
        result = {'tools': [{'name': f'tool_{n}', 'description': 'd' * 2048,
                            'inputSchema': {'type': 'object', 'properties': {}}}
                           for n in range(start, stop)]}
        if not start:
            result['nextCursor'] = 'page2'
    else:
        result = {'content': [{'type': 'text', 'text': 'ok'}]}
    print(json.dumps({'jsonrpc': '2.0', 'id': request['id'], 'result': result}), flush=True)
''')
    def definition(mode):
        return MCPServerDef(name=mode, transport='stdio', command=sys.executable,
                            args=[str(script), mode, str(tmp_path / f'{mode}.pid')])
    return definition


async def test_real_sdk_large_stdio_pages_and_call(stdio_server):
    manager = MCPManager(connect_timeout=5)
    try:
        conn = await manager.ensure(stdio_server('large'))
        assert len(conn.tools) == 500
        assert conn.tools[-1].name == 'tool_499'
        assert await manager.call('large', 'tool_499', {}) == 'ok'
    finally:
        await manager.aclose()
    assert not manager._tasks


async def test_real_stalled_subprocess_is_reaped(stdio_server, tmp_path):
    import os
    manager = MCPManager(connect_timeout=.5)
    try:
        with pytest.raises(TimeoutError, match='initialize'):
            await manager.ensure(stdio_server('stall'))
    finally:
        await manager.aclose()
    pid = int((tmp_path / 'stall.pid').read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_disconnect_one_server_reloads_new_tools_without_closing_others(transport):
    sessions, entered, exited = transport
    sessions.update(edited=Session(), other=Session())
    manager = MCPManager()
    try:
        await manager.ensure(server('edited'))
        other = await manager.ensure(server('other'))
        await manager.disconnect('edited')
        assert exited == ['edited']
        assert await manager.ensure(server('other')) is other
        sessions['edited'] = Session(pages={None: (['updated_tool'], None)})
        conn = await manager.ensure(server('edited'))
        assert [t.name for t in conn.tools] == ['updated_tool']
        assert entered == ['edited', 'other', 'edited']
    finally:
        await manager.aclose()


async def test_disconnect_pending_handshake_allows_reconnect(transport):
    sessions, _, _ = transport
    sessions['edited'] = Session(hang='initialize')
    manager = MCPManager()
    waiter = asyncio.create_task(manager.ensure(server('edited')))
    try:
        await sessions['edited'].started.wait()
        await manager.disconnect('edited')
        with pytest.raises(RuntimeError, match='cancelled'):
            await waiter
        sessions['edited'] = Session()
        await manager.ensure(server('edited'))
        assert 'edited' in manager._conns
    finally:
        await manager.aclose()
