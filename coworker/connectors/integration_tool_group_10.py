"""Bounded first-party integration tool builder partition."""

from __future__ import annotations

import json
from typing import Any, Callable, Optional
from urllib.parse import quote

from ..secrets import SecretStore
from . import integration_tools as _it


def add_tools(
    secrets: SecretStore,
    roots: Optional[list[Any]],
    tools: list[Callable[..., Any]],
) -> None:
        # --- Figma --------------------------------------------------------------

        _FIGMA = "https://api.figma.com/v1"

        def _figma_headers(profile: dict[str, Any]) -> dict[str, str]:
            return {"X-Figma-Token": str(profile.get("access_token", ""))}

        def _figma_summarize(node: dict[str, Any], depth: int) -> dict[str, Any]:
            out = {
                "id": node.get("id"),
                "name": node.get("name"),
                "type": node.get("type"),
            }
            children = node.get("children") or []
            if depth > 0 and children:
                out["children"] = [_figma_summarize(c, depth - 1) for c in children]
            elif children:
                out["child_count"] = len(children)
            return out

        def figma_get_file(file_key: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "figma", "access_token")
            if err:
                return err
            result = _it._request(
                "GET",
                f"{_FIGMA}/files/{quote(file_key)}",
                headers=_figma_headers(profile),
                params={"depth": 2},
            )
            if not result.get("ok"):
                return result
            data = result.get("data") or {}
            # The raw file tree is enormous — return pages + top-level frames only.
            doc = data.get("document") or {}
            return {
                "ok": True,
                "name": data.get("name"),
                "last_modified": data.get("lastModified"),
                "pages": [_figma_summarize(p, 1) for p in (doc.get("children") or [])],
            }

        figma_get_file.__name__ = "figma_get_file"
        tools.append(
            _it._attach(
                figma_get_file,
                _it._schema(
                    "figma_get_file",
                    "Read a Figma file's pages and top-level frames (file key is in the URL).",
                    {"file_key": {"type": "string"}},
                    ["file_key"],
                ),
                caps=["figma", "read"],
            )
        )

        def figma_get_comments(file_key: str) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "figma", "access_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_FIGMA}/files/{quote(file_key)}/comments",
                headers=_figma_headers(profile),
            )

        figma_get_comments.__name__ = "figma_get_comments"
        tools.append(
            _it._attach(
                figma_get_comments,
                _it._schema(
                    "figma_get_comments",
                    "List comments on a Figma file.",
                    {"file_key": {"type": "string"}},
                    ["file_key"],
                ),
                caps=["figma", "read"],
            )
        )

        def figma_post_comment(
            file_key: str, message: str, reply_to: str = ""
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "figma", "access_token")
            if err:
                return err
            body: dict[str, Any] = {"message": message}
            if reply_to:
                body["comment_id"] = reply_to
            return _it._request(
                "POST",
                f"{_FIGMA}/files/{quote(file_key)}/comments",
                headers=_figma_headers(profile),
                json=body,
            )

        figma_post_comment.__name__ = "figma_post_comment"
        tools.append(
            _it._attach(
                figma_post_comment,
                _it._schema(
                    "figma_post_comment",
                    "Comment on a Figma file (optionally replying to a comment). Requires user approval.",
                    {
                        "file_key": {"type": "string"},
                        "message": {"type": "string"},
                        "reply_to": {"type": "string"},
                    },
                    ["file_key", "message"],
                ),
                approval=True,
                caps=["figma", "write"],
            )
        )

        def figma_export_images(
            file_key: str, node_ids: str, format: str = "png", scale: int = 2
        ) -> dict[str, Any]:
            profile, err = _it._profile(secrets, "figma", "access_token")
            if err:
                return err
            return _it._request(
                "GET",
                f"{_FIGMA}/images/{quote(file_key)}",
                headers=_figma_headers(profile),
                params={"ids": node_ids, "format": format, "scale": scale},
            )

        figma_export_images.__name__ = "figma_export_images"
        tools.append(
            _it._attach(
                figma_export_images,
                _it._schema(
                    "figma_export_images",
                    "Render Figma nodes to image URLs (node ids comma-separated; png/svg/pdf).",
                    {
                        "file_key": {"type": "string"},
                        "node_ids": {"type": "string"},
                        "format": {"type": "string"},
                        "scale": {"type": "integer"},
                    },
                    ["file_key", "node_ids"],
                ),
                caps=["figma", "read"],
            )
        )

        # --- Google Drive (read-only; deliberately no write scope) ---------------

        _DRIVE = "https://www.googleapis.com/drive/v3"
        _DRIVE_FIELDS = "files(id,name,mimeType,modifiedTime,size,webViewLink)"
        # Google-native types export to text; everything else downloads as-is.
        _DRIVE_EXPORTS = {
            "application/vnd.google-apps.document": "text/plain",
            "application/vnd.google-apps.spreadsheet": "text/csv",
            "application/vnd.google-apps.presentation": "text/plain",
        }

        def _drive_quote(term: str) -> str:
            return term.replace("\\", "\\\\").replace("'", "\\'")

        def drive_search_files(
            query: str, max_results: int = 10, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "google_drive", account, "access_token"
            )
            if err:
                return err
            q = _drive_quote(query)
            return _it._acct_result(
                aid,
                _it._request(
                    "GET",
                    f"{_DRIVE}/files",
                    headers=_it._google_headers(profile["access_token"]),
                    params={
                        "q": f"(name contains '{q}' or fullText contains '{q}') and trashed=false",
                        "pageSize": _it._clamp(max_results),
                        "fields": _DRIVE_FIELDS,
                    },
                ),
            )

        drive_search_files.__name__ = "drive_search_files"
        tools.append(
            _it._attach(
                drive_search_files,
                _it._schema(
                    "drive_search_files",
                    "Search Google Drive files by name or content.",
                    {
                        "query": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["query"],
                ),
                caps=["google_drive", "read"],
            )
        )

        def drive_list_folder(
            folder_id: str = "root", max_results: int = 20, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "google_drive", account, "access_token"
            )
            if err:
                return err
            return _it._acct_result(
                aid,
                _it._request(
                    "GET",
                    f"{_DRIVE}/files",
                    headers=_it._google_headers(profile["access_token"]),
                    params={
                        "q": f"'{_drive_quote(folder_id)}' in parents and trashed=false",
                        "pageSize": _it._clamp(max_results, default=20, ceiling=50),
                        "fields": _DRIVE_FIELDS,
                    },
                ),
            )

        drive_list_folder.__name__ = "drive_list_folder"
        tools.append(
            _it._attach(
                drive_list_folder,
                _it._schema(
                    "drive_list_folder",
                    "List a Google Drive folder's contents ('root' for My Drive).",
                    {
                        "folder_id": {"type": "string"},
                        "max_results": {"type": "integer"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    [],
                ),
                caps=["google_drive", "read"],
            )
        )

        def drive_read_file(
            file_id: str, max_chars: int = 20000, account: str = ""
        ) -> dict[str, Any]:
            aid, profile, err = _it._account_profile(
                secrets, "google_drive", account, "access_token"
            )
            if err:
                return err
            headers = _it._google_headers(profile["access_token"])
            meta = _it._request(
                "GET",
                f"{_DRIVE}/files/{quote(file_id)}",
                headers=headers,
                params={"fields": "id,name,mimeType,size"},
            )
            if not meta.get("ok"):
                return _it._acct_result(aid, meta)
            info = meta.get("data") or {}
            mime = str(info.get("mimeType", ""))
            export_mime = _DRIVE_EXPORTS.get(mime)
            if export_mime:
                body = _it._request(
                    "GET",
                    f"{_DRIVE}/files/{quote(file_id)}/export",
                    headers=headers,
                    params={"mimeType": export_mime},
                )
            elif mime.startswith("application/vnd.google-apps"):
                return _it._acct_result(
                    aid, {"error": f"cannot read {mime} as text", "file": info}
                )
            else:
                body = _it._request(
                    "GET",
                    f"{_DRIVE}/files/{quote(file_id)}",
                    headers=headers,
                    params={"alt": "media"},
                )
            if not body.get("ok"):
                return _it._acct_result(aid, body)
            text = body.get("data")
            if not isinstance(text, str):
                text = json.dumps(text)
            return _it._acct_result(
                aid,
                {
                    "ok": True,
                    "file": info,
                    "content": text[: max(1, int(max_chars))],
                    "truncated": len(text) > max_chars,
                },
            )

        drive_read_file.__name__ = "drive_read_file"
        tools.append(
            _it._attach(
                drive_read_file,
                _it._schema(
                    "drive_read_file",
                    "Read a Drive file as text (Docs/Sheets/Slides export; other text files download).",
                    {
                        "file_id": {"type": "string"},
                        "max_chars": {"type": "integer"},
                        "account": _it._GEN_ACCOUNT_PROP,
                    },
                    ["file_id"],
                ),
                caps=["google_drive", "read"],
            )
        )
