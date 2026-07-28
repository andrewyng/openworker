"""New-flagship rollout (2026-07-14): GPT-5.6 Sol/Terra/Luna + Claude Fable 5 in the
matrix, both families' flagships as defaults, and friendly errors when an account can't
use them (GPT-5.6 rolls out per-organization; quota/credits can run out on any model).
"""

from coworker.config import Config
from coworker.providers.errors import friendly_model_error
from coworker.providers.matrix import MATRIX, models_for_provider
from coworker.providers.registry import get_descriptor


def test_new_flagships_in_matrix_with_labels():
    for mid, label in {
        "gpt-5.6-sol": "GPT-5.6 Sol · OpenAI",
        "gpt-5.6-terra": "GPT-5.6 Terra · OpenAI",
        "gpt-5.6-luna": "GPT-5.6 Luna · OpenAI",
        "anthropic:claude-fable-5": "Claude Fable 5 · Anthropic",
    }.items():
        assert MATRIX[mid].label == label
        assert MATRIX[mid].caps.tools and MATRIX[mid].caps.vision

    assert "gpt-5.6-sol" in models_for_provider("openai")
    assert "claude-fable-5" in models_for_provider("anthropic")


def test_flagships_are_the_defaults():
    assert Config().model == "gpt-5.6-sol"
    assert get_descriptor("openai").recommended_model == "gpt-5.6-sol"
    assert get_descriptor("anthropic").recommended_model == "claude-fable-5"


# -- friendly access/quota errors --------------------------------------------------------
def test_no_access_errors_are_translated():
    # OpenAI's 404/403 body for a model the org can't use yet
    exc = RuntimeError(
        "Error code: 404 - {'error': {'code': 'model_not_found', 'message': "
        "'The model `gpt-5.6-sol` does not exist or you do not have access to it.'}}"
    )
    msg = friendly_model_error("gpt-5.6-sol", exc)
    assert msg and "doesn't have access to gpt-5.6-sol" in msg

    # Anthropic's 404 body is type not_found_error + "model: <id>"
    exc = RuntimeError(
        "Error code: 404 - {'type': 'error', 'error': {'type': 'not_found_error', "
        "'message': 'model: claude-fable-5'}}"
    )
    msg = friendly_model_error("anthropic:claude-fable-5", exc)
    assert msg and "doesn't have access to anthropic:claude-fable-5" in msg


def test_quota_errors_are_translated():
    exc = RuntimeError(
        "Error code: 429 - {'error': {'code': 'insufficient_quota', 'message': "
        "'You exceeded your current quota, please check your plan and billing details.'}}"
    )
    msg = friendly_model_error("gpt-5.6-sol", exc)
    assert msg and "out of quota for gpt-5.6-sol" in msg

    exc = RuntimeError(
        "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
        "'message': 'Your credit balance is too low to access the Anthropic API.'}}"
    )
    msg = friendly_model_error("anthropic:claude-fable-5", exc)
    assert msg and "out of quota" in msg


def test_unrelated_errors_pass_through_raw():
    # a plain rate-limit (429 without a quota code) must NOT be dressed up
    assert (
        friendly_model_error(
            "gpt-5.6-sol",
            RuntimeError("Error code: 429 - rate_limit_exceeded, retry after 2s"),
        )
        is None
    )
    # a 404 from a wrong base_url isn't an access problem
    assert (
        friendly_model_error(
            "gpt-5.6-sol", RuntimeError("Error code: 404 - no route /v2/chat")
        )
        is None
    )
    assert (
        friendly_model_error("gpt-5.6-sol", RuntimeError("connection reset by peer"))
        is None
    )


# -- friendly authentication errors ------------------------------------------------------
def test_qwen_auth_error_mentions_endpoint_mismatch():
    # DashScope's 401 InvalidApiKey — the exact shape the OpenAI SDK surfaces when a
    # China-portal key hits the international endpoint (or vice versa).
    exc = RuntimeError(
        "Error code: 401 - {'code': 'InvalidApiKey', 'message': 'Invalid API-key "
        "provided.'}"
    )
    msg = friendly_model_error("qwen:qwen3-max", exc)
    assert msg is not None
    assert "qwen3-max" in msg
    assert "dashscope.aliyuncs.com" in msg
    assert "dashscope-intl.aliyuncs.com" in msg
    assert "endpoint" in msg.lower()


def test_qwen_auth_error_works_with_bare_model_name():
    # A bare "qwen3-max" (no provider prefix) should still be recognized as Qwen.
    exc = RuntimeError(
        "Error code: 401 - {'code': 'InvalidApiKey', 'message': 'Invalid API-key "
        "provided.'}"
    )
    msg = friendly_model_error("qwen3-max", exc)
    assert msg is not None
    assert "qwen3-max" in msg


def test_generic_auth_error_for_non_qwen_provider():
    # DeepSeek uses "invalid_api_key" — should get the generic key-check message,
    # not the Qwen-specific endpoint guidance.
    exc = RuntimeError(
        "Error code: 401 - {'error': {'code': 'invalid_api_key', 'message': "
        "'Incorrect API key provided.'}}"
    )
    msg = friendly_model_error("deepseek:deepseek-v4-pro", exc)
    assert msg is not None
    assert "deepseek-v4-pro" in msg
    assert "Settings" in msg
    # Must NOT mention DashScope endpoints for a non-Qwen provider
    assert "dashscope" not in msg.lower()


def test_permission_error_is_not_treated_as_auth_failure():
    # A 403 permission_error is about model access, not key validity — it must be
    # caught by _NO_ACCESS, not by the auth-failure path.
    exc = RuntimeError(
        "Error code: 403 - {'error': {'type': 'permission_error', 'message': "
        "'You do not have access to this model.'}}"
    )
    msg = friendly_model_error("qwen:qwen3-max", exc)
    assert msg is not None
    assert "doesn't have access" in msg
    assert "endpoint" not in msg.lower()


def test_unrelated_401_passes_through_raw():
    # A 401 that doesn't carry an invalid-key marker should NOT be dressed up.
    assert (
        friendly_model_error(
            "qwen:qwen3-max",
            RuntimeError("Error code: 401 - token expired, refresh required"),
        )
        is None
    )
