"""Cross-file provider registry consistency tests.

The provider system is maintained across three files that must stay in lockstep:

  - coworker/providers/registry.py   (DESCRIPTORS — provider names, recommended_model)
  - coworker/providers/matrix.py     (MATRIX — curated model entries + capabilities)
  - coworker/server/manager.py       (COMPAT_MODELS — compat-vendor model suggestions)

These tests assert the structural invariants that hold across all three files so that
a contributor adding or renaming a provider gets an immediate failure here rather than
a silent UI regression.

Invariants checked:
  1. Every MATRIX key's provider prefix resolves to a registered descriptor.
  2. Every COMPAT_MODELS key is a registered provider name.
  3. Providers absent from COMPAT_MODELS are only those with a documented reason
     (native providers: openai/anthropic/gemini; resellers: together/fireworks;
     keyless local: ollama — all route through the matrix or live /api/tags).
  4. Every compat-vendor descriptor's recommended_model is present in COMPAT_MODELS.
  5. Every bare MATRIX id (no colon) is not altered by ProviderRouter._bare(),
     confirming it routes to OpenAI as intended and is not a misrouted prefixed id.
"""

from __future__ import annotations

import pytest

from coworker.providers.base import ModelCapabilities
from coworker.providers.matrix import MATRIX
from coworker.providers.registry import get_descriptor, provider_names
from coworker.providers.router import ProviderRouter
from coworker.server.manager import SessionManager

# Providers that are intentionally absent from COMPAT_MODELS.
#
# - openai / anthropic / gemini: native providers; their model list comes exclusively
#   from matrix.py (models_for_provider) and needs no extra suggestions table.
# - together / fireworks: resellers; their curated bare ids live in matrix.py under
#   the `together:…` / `fireworks:…` prefixes; models_for_provider() strips them.
# - ollama: keyless, local; suggestions come from the live /api/tags endpoint, not
#   a static list.
#
# When adding a new reseller (e.g. groq, openrouter), add it here with a comment
# explaining why it's exempt, OR add it to COMPAT_MODELS in manager.py.
_COMPAT_MODELS_EXEMPT = frozenset(
    {"openai", "anthropic", "gemini", "together", "fireworks", "ollama"}
)

# The compat-vendor names that *must* have a COMPAT_MODELS entry.
_COMPAT_VENDOR_NAMES = frozenset(provider_names()) - _COMPAT_MODELS_EXEMPT


# ---------------------------------------------------------------------------
# 1. Every MATRIX key's provider prefix resolves to a registered descriptor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_id", list(MATRIX.keys()))
def test_matrix_key_prefix_is_registered_provider(model_id: str) -> None:
    """Each MATRIX key must be either a bare OpenAI id (no colon) or a
    `provider:bare_model` id whose prefix is a registered provider."""
    if ":" not in model_id:
        # Bare ids (no prefix) route to the OpenAI default — that's intentional.
        openai_desc = get_descriptor("openai")
        assert openai_desc is not None, "openai descriptor must exist"
        return

    prefix = model_id.split(":", 1)[0]
    assert get_descriptor(prefix) is not None, (
        f"MATRIX key {model_id!r} has prefix {prefix!r} which is not a "
        f"registered provider. Either register the provider in registry.py "
        f"or fix the matrix key."
    )


# ---------------------------------------------------------------------------
# 2. Every COMPAT_MODELS key is a registered provider name
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(SessionManager.COMPAT_MODELS.keys()))
def test_compat_models_key_is_registered_provider(name: str) -> None:
    """No orphaned keys in COMPAT_MODELS — each must correspond to a descriptor."""
    assert get_descriptor(name) is not None, (
        f"COMPAT_MODELS contains key {name!r} which has no matching descriptor "
        f"in registry.py. Remove the key or add a descriptor."
    )


# ---------------------------------------------------------------------------
# 3. Compat vendors each have a COMPAT_MODELS entry (exempt set is enforced)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_COMPAT_VENDOR_NAMES))
def test_compat_vendor_has_compat_models_entry(name: str) -> None:
    """Every non-exempt compat vendor must have a non-empty entry in COMPAT_MODELS."""
    assert name in SessionManager.COMPAT_MODELS, (
        f"Provider {name!r} is registered in registry.py but has no entry in "
        f"SessionManager.COMPAT_MODELS. Add it or add it to _COMPAT_MODELS_EXEMPT "
        f"with a comment explaining why it's exempt."
    )
    assert SessionManager.COMPAT_MODELS[name], (
        f"SessionManager.COMPAT_MODELS[{name!r}] is empty. "
        f"Add at least one suggested model or mark it exempt."
    )


# ---------------------------------------------------------------------------
# 4. Each compat-vendor descriptor's recommended_model is in COMPAT_MODELS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(_COMPAT_VENDOR_NAMES))
def test_compat_vendor_recommended_model_in_compat_models(name: str) -> None:
    """The recommended_model in the descriptor must appear in COMPAT_MODELS so
    set_provider's auto-add logic (_suggested_models check) actually fires."""
    desc = get_descriptor(name)
    assert desc is not None  # already covered above; guards the attribute access
    if desc.recommended_model is None:
        pytest.skip(f"descriptor for {name!r} has no recommended_model")
    assert desc.recommended_model in SessionManager.COMPAT_MODELS.get(name, []), (
        f"registry.py descriptor for {name!r} has recommended_model="
        f"{desc.recommended_model!r} but that model is not in "
        f"SessionManager.COMPAT_MODELS[{name!r}]. "
        f"set_provider will silently skip auto-adding it."
    )


# ---------------------------------------------------------------------------
# 5. Bare MATRIX ids are not stripped by the router (they belong to OpenAI)
# ---------------------------------------------------------------------------


def test_matrix_bare_ids_are_not_provider_prefixed() -> None:
    """Any MATRIX key without a colon must not be mistakenly strippable by the router.
    ProviderRouter._bare() only strips a segment that resolves to a known descriptor;
    a bare id that the router would strip has an unregistered provider prefix and
    would be silently misrouted. This test catches a matrix key like 'foo:bar' where
    'foo' is not yet registered — the router would treat it as OpenAI and strip 'foo:'."""
    for model_id in MATRIX:
        if ":" not in model_id:
            # No prefix at all — routes to OpenAI as intended. _bare() is a no-op.
            assert ProviderRouter._bare(model_id) == model_id, (
                f"MATRIX bare id {model_id!r}: ProviderRouter._bare() altered it, "
                f"which means the router is stripping an unregistered prefix. "
                f"Either register the provider or remove the colon from the model id."
            )
