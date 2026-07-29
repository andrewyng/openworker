"""The `repomap` tool — provides an AST-based repository map using Aider's logic.

This tool allows the agent to request a structural map of the codebase (classes, functions, etc.)
without relying on external semantic search or vector databases. It leverages `aider-chat`'s
highly compressed RepoMap generation.
"""

from __future__ import annotations

import os
from aisuite.agents import ToolMetadata, tool

try:
    from aider.repomap import RepoMap
except ImportError:
    RepoMap = None


def repomap_tool(workspace: str) -> list[object]:
    def generate_repomap(
        chat_files: list[str] | None = None,
        other_files: list[str] | None = None,
    ) -> dict:
        """Generate an AST-based structural map of the repository to understand the codebase.
        
        This tool parses the local files to generate a compressed map of classes, methods, and 
        variables. It is incredibly useful for understanding how different parts of the project
        interact before making a change.

        - `chat_files`: A list of file paths (relative to workspace) that are highly relevant to your task.
        - `other_files`: An optional list of other file paths (relative to workspace) to include for broader context.

        Returns a dictionary containing the `map`, which is a string representation of the repository's structure.
        """
        if RepoMap is None:
            return {"error": "The `aider-chat` package is not installed. RepoMap is unavailable."}
            
        chat_files = chat_files or []
        other_files = other_files or []

        # If no files are provided, map the entire repository
        if not chat_files and not other_files:
            import subprocess
            try:
                # Get all git-tracked files
                output = subprocess.check_output(
                    ["git", "ls-files"], 
                    cwd=workspace, 
                    text=True
                )
                other_files = [f for f in output.splitlines() if f.endswith(('.py', '.ts', '.js', '.tsx', '.jsx'))]
            except Exception:
                pass

        from aider.io import InputOutput

        class DummyModel:
            def token_count(self, text: str) -> int:
                return len(text) // 4

        try:
            # Instantiate RepoMap with a reasonable map token limit to prevent context bloat
            rm = RepoMap(root=workspace, map_tokens=1024, io=InputOutput(pretty=False), main_model=DummyModel())
            repo_map_str = rm.get_repo_map(
                chat_files=chat_files, 
                other_files=other_files
            )
            return {"map": repo_map_str or "No map could be generated. Check if files exist."}
        except Exception as e:
            return {"error": f"Failed to generate repo map: {str(e)}"}

    # Return as a list because coworker/agent.py calls register_all()
    return [
        tool(
            generate_repomap,
            metadata=ToolMetadata(
                category="filesystem",
                risk_level="low",
                capabilities=["repomap"],
                description=(
                    "Generate an AST-based structural map of the repository to understand codebase architecture. "
                    "Use this when you need a bird's-eye view of classes and functions across files."
                ),
            ),
        )
    ]
