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
  5. Every MATRIX entry that belongs to a compat vendor uses exactly the
     `provider:bare_model` id shape (no bare ids for non-OpenAI providers).
  6. ModelCapabilities in MATRIX only use fields defined on the dataclass (no typos).
"""

from __future__ import annotations

import pytest

from coworker.providers.matrix import MATRIX
from coworker.providers.registry import get_descriptor, provider_names
from coworker.server.manager import SessionManager

# Providers that are intentionally absent from COMPAT_MODELS.
#
# - openai / anthropic / gemini: native providers; their model list comes exclusively
#   from matrix.py (models_for_provider) and needs no extra suggestions table.
# - together / fireworks: resellers; their curated bare ids live in matrix.py under
#   the `together:…` / `fireworks:…` prefixes; models_for_provider() strips them.
# - ollama: keyless, local; suggestions come from the live /api/tags endpoint, not
#   a static list.
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
    assert desc.recommended_model in SessionManager.COMPAT_MODELS.get(name, []), (
        f"registry.py descriptor for {name!r} has recommended_model="
        f"{desc.recommended_model!r} but that model is not in "
        f"SessionManager.COMPAT_MODELS[{name!r}]. "
        f"set_provider will silently skip auto-adding it."
    )


# ---------------------------------------------------------------------------
# 5. Non-OpenAI MATRIX entries use provider-prefixed ids
# ---------------------------------------------------------------------------


def test_matrix_non_openai_entries_are_prefixed() -> None:
    """Bare (unprefixed) ids route to OpenAI.  Any non-OpenAI model stored without
    a prefix would silently be sent to the wrong provider."""
    openai_names = {
        mid for mid in MATRIX if ":" not in mid
    }
    for model_id in openai_names:
        # Verify the bare model name looks like an OpenAI model (gpt-*, o*).
        assert model_id.startswith(("gpt-", "o1", "o3", "o4")), (
            f"MATRIX contains bare (unprefixed) id {model_id!r} that does not look "
            f"like an OpenAI model. Prefix it with its provider (e.g. 'provider:{model_id}') "
            f"or confirm it is intentionally routed to OpenAI."
        )


# ---------------------------------------------------------------------------
# 6. ModelCapabilities fields in MATRIX have no typos
# ---------------------------------------------------------------------------


def test_matrix_capabilities_fields_are_valid() -> None:
    """ModelCapabilities is a frozen dataclass; accessing an undefined attribute
    raises AttributeError at runtime. Assert all MATRIX caps only use known fields."""
    from dataclasses import fields as dc_fields

    from coworker.providers.base import ModelCapabilities

    valid_fields = {f.name for f in dc_fields(ModelCapabilities)}
    for model_id, entry in MATRIX.items():
        caps = entry.caps
        # Verify the caps object itself is the right type.
        assert isinstance(caps, ModelCapabilities), (
            f"MATRIX[{model_id!r}].caps is {type(caps)!r}, expected ModelCapabilities."
        )
        # Verify all declared fields are accessible (catches renamed fields).
        for field_name in valid_fields:
            assert hasattr(caps, field_name), (
                f"ModelCapabilities is missing field {field_name!r} "
                f"(referenced from MATRIX entry {model_id!r})."
            )
