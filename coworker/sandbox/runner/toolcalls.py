"""Run one of OpenWorker's workspace tools inside the runner. Standard library only.

The tool DEFINITIONS (names, schemas, approval metadata) stay in the OpenWorker process.
When a session uses a sandbox, only the execution moves here: the server sends the tool's
name and arguments, this module runs the same code the in-process tool runs, and the result
goes back unchanged. So a session looks the same to the model in both modes.

The file-editing and git-status tools are aisuite's toolkit classes. They are plain
standard-library code, so the bundler packs the server's own copy of them into the runner
file (`aisuite_toolkits/`); run from a checkout, the installed aisuite is used instead.
"""

from __future__ import annotations

from typing import Any, Optional

from . import tools_git, tools_grep, tools_read

try:  # inside the packed runner
    from .aisuite_toolkits.files import FileToolkit
    from .aisuite_toolkits.git import GitToolkit
except ImportError:  # from a checkout (tests, development)
    from aisuite.toolkits.files import FileToolkit  # type: ignore[no-redef]
    from aisuite.toolkits.git import GitToolkit  # type: ignore[no-redef]

# The values `aisuite.toolkits.files()` / `.git()` use when OpenWorker builds these tools.
_MAX_READ_BYTES = 200_000
_MAX_SEARCH_BYTES = 1_000_000
_GIT_MAX_OUTPUT_CHARS = 20000

FILE_TOOLKIT_TOOLS = (
    "list_files",
    "read_file_lines",
    "search_files",
    "write_file",
    "apply_unified_diff",
    "apply_patch",
    "replace_in_file",
)
GIT_TOOLKIT_TOOLS = {"git_status": "status", "git_diff": "diff"}
OWN_TOOLS = ("read_file", "grep", "git_log")
TOOLS = (*FILE_TOOLKIT_TOOLS, *GIT_TOOLKIT_TOOLS, *OWN_TOOLS)


class ToolRaised(Exception):
    """The tool raised; the server re-raises the same kind of error with the same text."""

    def __init__(self, exc: BaseException) -> None:
        super().__init__(str(exc))
        self.type_name = type(exc).__name__


def call(
    name: str,
    args: dict[str, Any],
    *,
    workspace: str,
    roots: Optional[list[dict[str, Any]]] = None,
) -> Any:
    """`roots`: the session's folders as [{"path": ..., "writable": bool}], primary first.
    Without it the tools are scoped to `workspace` alone, writable (single-root sessions)."""
    try:
        if name == "read_file":
            extra = [str(r["path"]) for r in (roots or [])]
            return tools_read.read_file(workspace, roots=extra, **args)
        if name == "grep":
            return tools_grep.grep(workspace, **args)
        if name == "git_log":
            return tools_git.git_log(workspace, **args)
        if name in GIT_TOOLKIT_TOOLS:
            kit = GitToolkit(root=workspace, max_output_chars=_GIT_MAX_OUTPUT_CHARS)
            return getattr(kit, GIT_TOOLKIT_TOOLS[name])(**args)
        if name in FILE_TOOLKIT_TOOLS:
            scope = {"roots": roots} if roots else {"root": workspace, "allow_write": True}
            kit = FileToolkit(
                max_read_bytes=_MAX_READ_BYTES, max_search_bytes=_MAX_SEARCH_BYTES, ignore=None, **scope
            )
            return getattr(kit, name)(**args)
    except Exception as exc:
        raise ToolRaised(exc) from exc
    raise ToolRaised(KeyError(f"not a workspace tool: {name}"))
