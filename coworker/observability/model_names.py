"""One name per model when recordings are read and grouped (T3-4).

Sessions saved before the model-prefix fix (OPE-247, PR #837) recorded the same OpenAI model as
both `openai:gpt-5.6-sol` and `gpt-5.6-sol`, so Usage showed it twice and split its tokens.
Recordings are never rewritten; they are read through this rule.

`canonical_model` is the same rule, line for line, as `coworker/providers/router.py`
`canonical_model` on issue/ope-247-openai-model-prefix (and the GUI's `canonicalModel` in
`surfaces/gui/src/modelNames.ts`), so the two agree when both land. Once #837 is merged this
module can import it from there.
"""

from __future__ import annotations

# The provider a model id with no `provider:` prefix runs on.
DEFAULT_PROVIDER = "openai"


def canonical_model(model: str) -> str:
    """The one spelling of a model id: a prefix naming the default provider says nothing a
    bare id doesn't (`openai:gpt-5.5` and `gpt-5.5` both run on OpenAI), so it is dropped.
    Every other prefix (`anthropic:`, `ollama:` ...) is kept."""
    model = (model or "").strip()
    prefix = DEFAULT_PROVIDER + ":"
    if model.startswith(prefix) and len(model) > len(prefix):
        return model[len(prefix):]
    return model


# The same rule in SQL, for grouping the usage ledger by model (`u.model`).
# (an exact, case-sensitive prefix test, as the Python rule; SQLite's LIKE ignores case)
_PREFIX_LEN = len(DEFAULT_PROVIDER) + 1
CANONICAL_MODEL_SQL = (f"CASE WHEN substr(u.model, 1, {_PREFIX_LEN}) = '{DEFAULT_PROVIDER}:' "
                       f"AND length(u.model) > {_PREFIX_LEN} THEN substr(u.model, {_PREFIX_LEN + 1}) "
                       "ELSE u.model END")
