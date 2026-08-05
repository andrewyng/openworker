"""Model-fetch candidate URL generation — ported from cc-switch's `model_fetch.rs`.

`build_models_candidates` generates the list of URLs to try when fetching a provider's
model list, handling the same edge cases cc-switch does: known compat suffixes, version
segment endings, and full-URL parsing.
"""

from __future__ import annotations

import re
from typing import Optional

# Suffixes that indicate a _compatibility_ sub-path — the real `/v1/models` endpoint
# lives at the root, not under this path. Ported from cc-switch's `KNOWN_COMPAT_SUFFIXES`.
KNOWN_COMPAT_SUFFIXES: list[str] = [
    "/api/claudecode",
    "/api/anthropic",
    "/apps/anthropic",
    "/api/coding",
    "/claudecode",
    "/anthropic",
    "/step_plan",
    "/coding",
    "/claude",
]

# Version-segment patterns: if the URL ends with one of these, the `/models` endpoint
# lives at the same level (i.e. strip the version segment, not append).
_VERSION_SEGMENTS = {"/v1", "/v4", "/v2", "/v1beta", "/v1beta1", "/v1beta2"}


def _strip_compat_suffix(url: str) -> Optional[str]:
    """If `url` ends with a known compat suffix, return the URL without it."""
    for suffix in KNOWN_COMPAT_SUFFIXES:
        if url.endswith(suffix):
            return url[: -len(suffix)]
    return None


def build_models_candidates(
    base_url: str,
    *,
    is_full_url: bool = False,
    models_url_override: Optional[str] = None,
) -> list[str]:
    """Build the list of candidate URLs to try when fetching models.

    Mirrors cc-switch's `build_models_url_candidates` (model_fetch.rs:139-200).

    Args:
        base_url: The provider's base URL (e.g. ``https://api.deepseek.com``).
        is_full_url: If True, treat ``base_url`` as a full request URL; extract the path
            prefix and append ``/v1/models``.
        models_url_override: If set, use ONLY this URL (e.g. DeepSeek's ``/models``
            lives at the root path).

    Returns:
        A list of candidate URLs, in order of preference.
    """
    candidates: list[str] = []

    # 1. Explicit override — use as-is, no further guessing.
    if models_url_override:
        return [models_url_override]

    url = base_url.strip().rstrip("/")

    # 2. Full-URL mode: extract the path prefix (e.g. ``/api/paas/v4`` from
    #    ``https://api.z.ai/api/paas/v4``) and append ``/v1/models``.
    if is_full_url:
        # Extract the path from the URL (everything after the host)
        match = re.match(r"https?://[^/]+(/.*)", url)
        if match:
            path_prefix = match.group(1).rstrip("/")
            candidates.append(url + "/v1/models")
            # Also try: if the path prefix looks like a version segment, try
            # replacing it with ``/v1/models``.
            # cc-switch does: `format!("{path_prefix}/v1/models")`
            # Actually cc-switch does: `format!("{url}/v1/models")` for is_full_url
            # Let me re-read the Rust code...
            # The Rust code:
            #   if is_full_url {
            #       candidates.push(format!("{url}/v1/models"));
            #       // also try stripping the last path segment
            #       if let Some(parent) = Path::new(path_prefix).parent() {
            #           candidates.push(format!("{}{}/v1/models", origin, parent.display()));
            #       }
            #   }
            # So it first tries {url}/v1/models, then {origin}{parent}/v1/models
            candidates.append(url + "/v1/models")
            # Also try parent path
            parts = path_prefix.rsplit("/", 1)
            if len(parts) > 1:
                parent = parts[0]
                candidates.append(url[: len(url) - len(path_prefix)] + parent + "/v1/models")
        else:
            candidates.append(url + "/v1/models")
        # Also try the root path
        candidates.append(url + "/models")
        return candidates

    # 3. Normal mode: start with ``{base}/v1/models``
    candidates.append(url + "/v1/models")

    # 4. If the URL ends with a version segment (``/v1``, ``/v4``, etc.), also try
    #    ``{base}/models`` (i.e. strip the version segment rather than appending).
    for vs in _VERSION_SEGMENTS:
        if url.endswith(vs):
            candidates.append(url + "/models")
            break

    # 5. Check for known compat suffixes — strip them and retry with the root.
    stripped = _strip_compat_suffix(url)
    if stripped:
        candidates.append(stripped + "/v1/models")
        candidates.append(stripped + "/models")

    # 6. Last resort: try the bare root path
    candidates.append(url + "/models")

    return candidates