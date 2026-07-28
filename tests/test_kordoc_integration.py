"""Manager boundary tests for the pinned read-only Korean-document integration."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from coworker.integrations.kordoc import KORDOC_MCP_TOOL_ALLOWLIST
from coworker.mcp.config import MCPServerDef, put_global_server
from coworker.server import manager as manager_module
from coworker.server.manager import SessionManager
from coworker.sessions import SessionRecord


RAG_TOOLS = [
    "detect_format",
    "parse_metadata",
    "parse_chunks",
    "parse_pages",
    "parse_table",
]


def _manager(tmp_path: Path, monkeypatch) -> SessionManager:
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return SessionManager(workspace=workspace, data_dir=tmp_path / "data")


def _tool(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        description=f"Kordoc {name}",
        inputSchema={"type": "object", "properties": {"file_path": {"type": "string"}}},
    )


def _trusted_kordoc_server() -> MCPServerDef:
    return MCPServerDef(
        name="kordoc",
        transport="stdio",
        command="trusted-node",
        args=["trusted-kordoc-mcp.js"],
        include_tools=list(KORDOC_MCP_TOOL_ALLOWLIST),
        requires_approval=True,
    )


def test_korean_docs_uses_only_builtin_kordoc_and_forces_approval(
    tmp_path, monkeypatch
):
    assert KORDOC_MCP_TOOL_ALLOWLIST == RAG_TOOLS
    manager = _manager(tmp_path, monkeypatch)
    manager.session_store.save(
        SessionRecord(
            session_id="korean-session",
            workspace=str(tmp_path / "workspace"),
            model="gpt-5.6-sol",
            mode="interactive",
            agent="korean-docs",
        )
    )
    put_global_server(
        "kordoc",
        {
            "command": "untrusted-command",
            "args": ["untrusted-kordoc.js"],
            "include_tools": ["untrusted_extra"],
            "requires_approval": False,
        },
    )
    put_global_server("other", {"command": "untrusted-other"})
    config_path = tmp_path / "state" / "mcp.json"
    config_before = config_path.read_text(encoding="utf-8")

    trusted = _trusted_kordoc_server()
    trusted.include_tools = ["untrusted_extra"]
    trusted.requires_approval = False
    monkeypatch.setattr(manager_module, "builtin_kordoc_server", lambda: trusted)
    offloaded: list[object] = []

    async def offload(function, *args, **kwargs):
        offloaded.append(function)
        return function(*args, **kwargs)

    monkeypatch.setattr(manager_module.asyncio, "to_thread", offload)
    monkeypatch.setattr(
        manager_module,
        "load_mcp_servers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Korean Documents must not load configured MCP servers")
        ),
    )
    seen: list[MCPServerDef] = []

    async def ensure(server, *, connection_key=None):
        seen.append(server)
        assert connection_key is manager_module._BUILTIN_KORDOC_CONNECTION_KEY
        return SimpleNamespace(
            tools=[*[_tool(name) for name in RAG_TOOLS], _tool("untrusted_extra")]
        )

    monkeypatch.setattr(manager.mcp, "ensure", ensure)

    # A persisted persona wins over a reconnect's omitted/default agent query.
    tools = asyncio.run(manager.prepare_mcp_tools("korean-session", agent="code"))

    assert len(seen) == 1
    assert seen[0].name == "kordoc"
    assert seen[0].command == trusted.command
    assert seen[0].args == trusted.args
    assert trusted.name == "kordoc"
    assert offloaded == [manager_module.builtin_kordoc_server]
    assert trusted.command == "trusted-node"
    assert trusted.args == ["trusted-kordoc-mcp.js"]
    assert trusted.include_tools == RAG_TOOLS
    assert trusted.requires_approval is True
    assert config_path.read_text(encoding="utf-8") == config_before
    assert [tool.__aisuite_tool_metadata__.name for tool in tools] == [
        f"mcp__kordoc__{name}" for name in RAG_TOOLS
    ]
    assert all(tool.__aisuite_tool_metadata__.requires_approval for tool in tools)


def test_kordoc_rag_paths_are_canonicalized_before_any_mcp_call(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    source = workspace / "source.hwp"
    source.write_bytes(b"source")
    outside = tmp_path / "outside.hwp"
    outside.write_bytes(b"outside")
    folder = workspace / "not-a-document"
    folder.mkdir()

    trusted = _trusted_kordoc_server()
    monkeypatch.setattr(manager_module, "builtin_kordoc_server", lambda: trusted)
    connection = SimpleNamespace(tools=[*[_tool(name) for name in RAG_TOOLS], _tool("write")])

    async def ensure(_server, *, connection_key=None):
        assert connection_key is manager_module._BUILTIN_KORDOC_CONNECTION_KEY
        return connection

    calls: list[tuple[str, dict]] = []

    async def call_on_connection(_connection, tool, arguments):
        calls.append((tool, arguments))
        return {"ok": tool}

    async def must_not_call(*_args, **_kwargs):
        raise AssertionError("Kordoc call must be bound to its prepared connection")

    monkeypatch.setattr(manager.mcp, "ensure", ensure)
    monkeypatch.setattr(manager.mcp, "call_on_connection", call_on_connection)
    monkeypatch.setattr(manager.mcp, "call", must_not_call)

    async def exercise():
        tools = await manager.prepare_mcp_tools(
            "korean", workspace=str(workspace), agent="korean-docs"
        )
        by_remote = {
            tool.__name__.removeprefix("mcp__kordoc__"): tool for tool in tools
        }
        assert list(by_remote) == RAG_TOOLS

        for remote, tool in by_remote.items():
            result = await asyncio.to_thread(tool, file_path="source.hwp", page=1)
            assert result == {"ok": remote}
        assert calls == [
            (remote, {"file_path": str(source.resolve()), "page": 1})
            for remote in RAG_TOOLS
        ]

        rejected = [
            ("parse_metadata", {"file_path": str(outside)}),
            ("parse_chunks", {"file_path": "../outside.hwp"}),
            ("parse_pages", {"file_path": ["source.hwp"]}),
            ("parse_table", {"file_path": "not-a-document"}),
            ("detect_format", {}),
        ]
        for remote, arguments in rejected:
            before = len(calls)
            result = await asyncio.to_thread(by_remote[remote], **arguments)
            assert "error" in result
            assert "session workspace" in result["error"]
            assert len(calls) == before

        escaped = workspace / "escaped.hwp"
        try:
            escaped.symlink_to(outside)
        except OSError:
            pytest.skip("file symlinks are unavailable for this test account")
        before = len(calls)
        result = await asyncio.to_thread(
            by_remote["detect_format"], file_path="escaped.hwp"
        )
        assert "error" in result and "session workspace" in result["error"]
        assert len(calls) == before

    asyncio.run(exercise())


@pytest.mark.skipif(os.name != "nt", reason="NTFS junction semantics")
def test_kordoc_rejects_junction_escape(tmp_path):
    from coworker.mcp.tools import validate_kordoc_workspace_arguments

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "document.hwp").write_bytes(b"outside")
    junction = workspace / "outside-junction"
    created = subprocess.run(
        [
            "cmd.exe",
            "/d",
            "/c",
            "mklink",
            "/J",
            str(junction),
            str(outside),
        ],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
        shell=False,
    )
    if created.returncode != 0:
        pytest.skip("NTFS junction creation is unavailable for this test account")

    arguments = {"file_path": "outside-junction/document.hwp"}
    error = validate_kordoc_workspace_arguments(
        "parse_chunks", arguments, workspace
    )

    assert error is not None
    assert "outside the session workspace" in error


@pytest.mark.skipif(os.name != "nt", reason="Windows UNC path semantics")
def test_kordoc_rejects_unc_before_resolving_requested_path(tmp_path, monkeypatch):
    from coworker.mcp.tools import validate_kordoc_workspace_arguments

    original_resolve = Path.resolve

    def guarded_resolve(path, *args, **kwargs):
        if str(path).startswith("\\\\"):
            raise AssertionError("UNC path was resolved before the trust decision")
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", guarded_resolve)
    arguments = {"file_path": r"\\attacker.invalid\share\document.hwp"}

    error = validate_kordoc_workspace_arguments(
        "parse_chunks", arguments, tmp_path
    )

    assert error is not None
    assert "outside the session workspace" in error
    assert arguments["file_path"].startswith("\\\\")


@pytest.mark.parametrize("stale_workspace", [False, True])
def test_korean_docs_provisions_its_engine_scratch_before_binding_path_guard(
    tmp_path, monkeypatch, stale_workspace
):
    monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
    manager = SessionManager(workspace=None, data_dir=tmp_path / "data")
    scratch_base = tmp_path / "scratch"
    manager._prefs["scratch_base"] = str(scratch_base)
    manager._save_prefs()
    if stale_workspace:
        manager.session_store.save(
            SessionRecord(
                session_id="fresh",
                workspace=str(tmp_path / "missing-workspace"),
                model="gpt-5.6-sol",
                mode="interactive",
                agent="korean-docs",
            )
        )
    trusted = _trusted_kordoc_server()
    monkeypatch.setattr(manager_module, "builtin_kordoc_server", lambda: trusted)
    connection = SimpleNamespace(tools=[_tool("parse_metadata")])
    calls: list[tuple[str, dict]] = []

    async def ensure(_server, *, connection_key=None):
        assert connection_key is manager_module._BUILTIN_KORDOC_CONNECTION_KEY
        return connection

    async def call_on_connection(_connection, tool, arguments):
        calls.append((tool, arguments))
        return {"ok": tool}

    monkeypatch.setattr(manager.mcp, "ensure", ensure)
    monkeypatch.setattr(manager.mcp, "call_on_connection", call_on_connection)

    async def exercise():
        tools = await manager.prepare_mcp_tools(
            "fresh", agent="code" if stale_workspace else "korean-docs"
        )
        scratch = scratch_base / "fresh"
        assert scratch.is_dir()
        source = scratch / "source.hwp"
        source.write_bytes(b"source")
        parse_metadata = next(
            tool for tool in tools if tool.__name__.endswith("parse_metadata")
        )
        assert await asyncio.to_thread(parse_metadata, file_path="source.hwp") == {
            "ok": "parse_metadata"
        }
        assert calls == [
            ("parse_metadata", {"file_path": str(source.resolve())})
        ]

        engine = manager.get_engine(
            "fresh",
            agent="code" if stale_workspace else "korean-docs",
            extra_tools=tools,
        )
        assert engine is not None
        assert engine.audit_context["workspace"] == str(scratch.resolve())

    asyncio.run(exercise())


def test_configured_kordoc_and_builtin_kordoc_keep_distinct_connections(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    source = workspace / "source.hwp"
    source.write_bytes(b"source")
    put_global_server(
        "kordoc",
        {
            "command": "configured-node",
            "args": ["configured-mcp.js"],
            "include_tools": ["parse_chunks"],
        },
    )
    put_global_server(
        "__openworker_kordoc_rag__",
        {
            "command": "legacy-name-node",
            "args": ["legacy-name-mcp.js"],
            "include_tools": ["parse_chunks"],
        },
    )
    monkeypatch.setattr(
        manager_module, "builtin_kordoc_server", _trusted_kordoc_server
    )
    starts: list[tuple[str, object, str]] = []

    class FakeSession:
        def __init__(self, command):
            self.command = command

        async def call_tool(self, tool, arguments):
            return SimpleNamespace(
                content=[SimpleNamespace(text=f"{self.command}:{tool}")],
                isError=False,
                structuredContent=None,
            )

    async def fake_serve(server, ready, *, interactive=False, connection_key=None):
        del interactive
        starts.append((server.name, connection_key, server.command))
        connection = SimpleNamespace(
            session=FakeSession(server.command),
            tools=[_tool("parse_chunks")],
            shutdown=asyncio.Event(),
        )
        ready.set_result(connection)
        await connection.shutdown.wait()

    monkeypatch.setattr(manager.mcp, "_serve", fake_serve)

    async def exercise():
        configured = await manager.prepare_mcp_tools(
            "configured", workspace=str(workspace), agent="cowork"
        )
        builtin = await manager.prepare_mcp_tools(
            "builtin", workspace=str(workspace), agent="korean-docs"
        )
        configured_parse = next(
            tool for tool in configured if tool.__name__.endswith("parse_chunks")
        )
        builtin_parse = next(
            tool for tool in builtin if tool.__name__.endswith("parse_chunks")
        )

        assert await asyncio.to_thread(
            configured_parse, file_path="source.hwp"
        ) == "configured-node:parse_chunks"
        assert await asyncio.to_thread(
            builtin_parse, file_path="source.hwp"
        ) == "trusted-node:parse_chunks"
        assert starts == [
            ("kordoc", "kordoc", "configured-node"),
            (
                "__openworker_kordoc_rag__",
                "__openworker_kordoc_rag__",
                "legacy-name-node",
            ),
            ("kordoc", manager_module._BUILTIN_KORDOC_CONNECTION_KEY, "trusted-node"),
        ]
        assert set(manager.mcp._conns) == {
            "kordoc",
            "__openworker_kordoc_rag__",
            manager_module._BUILTIN_KORDOC_CONNECTION_KEY,
        }
        builtin_connection = manager.mcp._conns[
            manager_module._BUILTIN_KORDOC_CONNECTION_KEY
        ]
        await manager.signout_mcp("kordoc")
        await manager.signout_mcp("__openworker_kordoc_rag__")
        assert not builtin_connection.shutdown.is_set()
        assert await asyncio.to_thread(
            builtin_parse, file_path="source.hwp"
        ) == "trusted-node:parse_chunks"
        await manager.aclose()

    asyncio.run(exercise())


def test_korean_sessions_share_pinned_connection_and_bound_callables(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path, monkeypatch)
    workspace = tmp_path / "workspace"
    source = workspace / "source.hwp"
    source.write_bytes(b"source")
    trusted = _trusted_kordoc_server()
    monkeypatch.setattr(manager_module, "builtin_kordoc_server", lambda: trusted)

    starts: list[str] = []
    calls: list[tuple[str, dict]] = []

    class FakeSession:
        async def call_tool(self, tool, arguments):
            calls.append((tool, arguments))
            return SimpleNamespace(
                content=[SimpleNamespace(text="trusted")],
                isError=False,
                structuredContent=None,
            )

    async def fake_serve(server, ready, *, interactive=False, connection_key=None):
        del interactive
        assert connection_key is manager_module._BUILTIN_KORDOC_CONNECTION_KEY
        starts.append(server.command)
        connection = SimpleNamespace(
            session=FakeSession(),
            tools=[_tool("parse_metadata")],
            shutdown=asyncio.Event(),
        )
        ready.set_result(connection)
        await connection.shutdown.wait()

    monkeypatch.setattr(manager.mcp, "_serve", fake_serve)

    async def exercise():
        first, second = await asyncio.gather(
            manager.prepare_mcp_tools(
                "korean-one", workspace=str(workspace), agent="korean-docs"
            ),
            manager.prepare_mcp_tools(
                "korean-two", workspace=str(workspace), agent="korean-docs"
            ),
        )
        assert starts == ["trusted-node"]
        first_parse = next(
            tool for tool in first if tool.__name__.endswith("parse_metadata")
        )
        second_parse = next(
            tool for tool in second if tool.__name__.endswith("parse_metadata")
        )
        assert await asyncio.to_thread(first_parse, file_path="source.hwp") == "trusted"
        assert await asyncio.to_thread(second_parse, file_path="source.hwp") == "trusted"
        assert calls == [
            ("parse_metadata", {"file_path": str(source.resolve())}),
            ("parse_metadata", {"file_path": str(source.resolve())}),
        ]
        await manager.aclose()

    asyncio.run(exercise())


def test_korean_docs_runtime_unavailable_skips_without_using_config(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    put_global_server("kordoc", {"command": "untrusted-command"})
    config_path = tmp_path / "state" / "mcp.json"
    config_before = config_path.read_text(encoding="utf-8")
    monkeypatch.setattr(manager_module, "builtin_kordoc_server", lambda: None)
    monkeypatch.setattr(
        manager_module,
        "load_mcp_servers",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Korean Documents must not load configured MCP servers")
        ),
    )

    async def must_not_connect(server, *, connection_key=None):
        del connection_key
        raise AssertionError(f"unexpected connection: {server.name}")

    monkeypatch.setattr(manager.mcp, "ensure", must_not_connect)

    manager._mcp_errors["kordoc"] = "configured server error"

    assert asyncio.run(manager.prepare_mcp_tools("new", agent="korean-docs")) == []
    assert manager._mcp_errors["kordoc"] == "configured server error"
    assert "install" in manager._kordoc_mcp_error.lower()
    assert str(tmp_path) not in manager._kordoc_mcp_error
    assert config_path.read_text(encoding="utf-8") == config_before


def test_other_personas_keep_configured_mcp_behavior(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    put_global_server("notes", {"command": "notes-server"})

    def must_not_build_kordoc():
        raise AssertionError("non-Korean persona unexpectedly selected Kordoc")

    monkeypatch.setattr(manager_module, "builtin_kordoc_server", must_not_build_kordoc)
    seen: list[str] = []

    async def ensure(server):
        seen.append(server.name)
        return SimpleNamespace(tools=[_tool("read_note")])

    monkeypatch.setattr(manager.mcp, "ensure", ensure)

    tools = asyncio.run(manager.prepare_mcp_tools("new", agent="cowork"))

    assert seen == ["notes"]
    assert [tool.__name__ for tool in tools] == ["mcp__notes__read_note"]
