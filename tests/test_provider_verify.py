"""Tests for provider key detection + the live Test/verify path. SDK-free: httpx.get — and
httpx.post, which only NVIDIA NIM needs — are monkeypatched so no network is touched."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from coworker.providers import detect_provider, verify_provider_key


# -- detect_provider ------------------------------------------------------------
@pytest.mark.parametrize(
    "key,expected",
    [
        ("sk-ant-api03-abc", "anthropic"),
        ("sk-orca-v1-abc", "orcarouter"),
        ("sk-or-v1-abc", "openrouter"),
        ("AIzaSyAbc123", "gemini"),
        ("nvapi-abc123", "nvidia"),
        ("sk-proj-abc", "openai"),
        ("sk_live_abc", "openai"),
        ("", None),
        ("   ", None),
        ("nonsense", None),
    ],
)
def test_detect_provider(key, expected):
    assert detect_provider(key) == expected


# -- verify_provider_key: status-code mapping + per-provider request shape -------
def _patch_get(monkeypatch, status=200, capture=None, raise_exc=None, route_status=None):
    """Patch httpx.get. `status` answers the first (probe) request; `route_status`
    (defaulting to `status`) answers the OpenAI-compatible completions-route check."""

    def fake_get(url, **kwargs):
        if capture is not None:
            if "calls" not in capture:
                capture["calls"] = []
            capture["calls"].append({"url": url, **kwargs})
            # Keep the pre-existing single-call shape for the probe request.
            if "/chat/completions" not in url:
                capture.update(kwargs)
                capture["url"] = url
        if raise_exc is not None:
            raise raise_exc
        if "/chat/completions" in url:
            return SimpleNamespace(status_code=route_status or status)
        return SimpleNamespace(status_code=status)

    monkeypatch.setattr("httpx.get", fake_get)


def _patch_post(monkeypatch, status=200, capture=None, raise_exc=None):
    def fake_post(url, **kwargs):
        if capture is not None:
            capture["url"] = url
            capture.update(kwargs)
        if raise_exc is not None:
            raise raise_exc
        return SimpleNamespace(status_code=status)

    monkeypatch.setattr("httpx.post", fake_post)


def test_verify_openai_ok(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    assert verify_provider_key("openai", api_key="sk-x") == {"ok": True}
    assert cap["url"] == "https://api.openai.com/v1/models"
    assert cap["headers"]["Authorization"] == "Bearer sk-x"
    # The completions-route probe follows the /models reachability check.
    assert [c["url"] for c in cap["calls"]] == [
        "https://api.openai.com/v1/models",
        "https://api.openai.com/v1/chat/completions",
    ]


def test_verify_openai_custom_endpoint(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key(
        "openai", api_key="sk-x", base_url="https://gw.example/openai/v1/"
    )
    # trailing slash trimmed, /models appended to the custom endpoint
    assert cap["url"] == "https://gw.example/openai/v1/models"
    assert cap["calls"][1]["url"] == "https://gw.example/openai/v1/chat/completions"


def test_verify_openai_completions_route_404_is_clear(monkeypatch):
    """A base URL that answers /models but 404s on /chat/completions (missing /v1 path
    segment) must fail the Test with a path-specific message, not report success and
    then hang on every task (#431)."""
    cap: dict = {}
    _patch_get(monkeypatch, status=200, route_status=404, capture=cap)
    res = verify_provider_key("openai", api_key="sk-x", base_url="http://127.0.0.1:1234")
    assert res["ok"] is False
    assert "completions route 404s" in res["error"]
    assert "v1" in res["error"]


def test_verify_orcarouter_endpoint(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    assert verify_provider_key("orcarouter", api_key="sk-orca-x") == {"ok": True}
    # OpenAI-compatible branch: prefilled vendor endpoint, bearer key
    assert cap["url"] == "https://api.orcarouter.ai/v1/models"
    assert cap["headers"]["Authorization"] == "Bearer sk-orca-x"


def test_verify_novita_uses_openai_compatible_endpoint(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    assert verify_provider_key("novita", api_key="sk-nov") == {"ok": True}
    assert cap["url"] == "https://api.novita.ai/openai/v1/models"
    assert cap["headers"]["Authorization"] == "Bearer sk-nov"


def test_verify_bad_key_is_invalid(monkeypatch):
    _patch_get(monkeypatch, status=401)
    assert verify_provider_key("openai", api_key="sk-bad") == {
        "ok": False,
        "error": "Invalid API key.",
    }


def test_verify_anthropic_headers(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("anthropic", api_key="sk-ant-x")
    assert cap["url"] == "https://api.anthropic.com/v1/models"
    assert cap["headers"]["x-api-key"] == "sk-ant-x"
    assert "anthropic-version" in cap["headers"]


def test_verify_gemini_key_param(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("gemini", api_key="AIza-x")
    assert cap["params"]["key"] == "AIza-x"


def test_verify_ollama_uses_v1_models_no_key(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key("ollama", base_url="http://localhost:11434")
    assert cap["url"] == "http://localhost:11434/v1/models"
    assert "headers" not in cap  # keyless


# -- NVIDIA NIM: the one provider a list-models probe can't verify ---------------
def _patch_post_refusing_get(monkeypatch, status=200, capture=None):
    _patch_post(monkeypatch, status=status, capture=capture)

    def refuse_get(*_a, **_kw):  # see the docstring below
        raise AssertionError("NVIDIA verify must not GET /models — it is unauthenticated")

    monkeypatch.setattr("httpx.get", refuse_get)


def test_verify_nvidia_posts_a_one_token_completion(monkeypatch):
    """NIM's /v1/models is PUBLIC — 200 for any key, including an empty one — so a
    list-models probe would greenlight a typo. Auth is only enforced on inference, hence
    the POST. The GET stub above fails the test if this ever regresses to the generic
    branch."""
    from coworker.providers.registry import get_descriptor

    cap: dict = {}
    _patch_post_refusing_get(monkeypatch, status=200, capture=cap)
    assert verify_provider_key("nvidia", api_key="nvapi-x") == {"ok": True}
    assert cap["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert cap["headers"]["Authorization"] == "Bearer nvapi-x"
    # Asserted against the descriptor, not a literal: NIM model ids rotate, and the point
    # is that verify names the *recommended* model — a bad id 404s before auth is checked.
    assert cap["json"]["model"] == get_descriptor("nvidia").recommended_model
    assert cap["json"]["max_tokens"] == 1


def test_verify_nvidia_bad_key_is_invalid(monkeypatch):
    """NIM answers 403 on a bad key (401 with no header at all); both must read as a key
    problem, not as an unreachable server."""
    _patch_post_refusing_get(monkeypatch, status=403)
    assert verify_provider_key("nvidia", api_key="nvapi-bad") == {
        "ok": False,
        "error": "Invalid API key.",
    }


@pytest.mark.parametrize(
    "name,base_url,model",
    [
        (
            "ark",
            "https://ark.ap-southeast.bytepluses.com/api/v3",
            "dola-seed-evolving-latest-version",
        ),
        (
            "ark-agent-plan-cn",
            "https://ark.cn-beijing.volces.com/api/plan/v3",
            "doubao-seed-evolving",
        ),
    ],
)
def test_verify_ark_uses_non_persisted_responses_probe(
    monkeypatch, name, base_url, model
):
    """Reverse-verified probe: the captured fixture must be non-empty and provider-specific."""
    cap: dict = {}
    _patch_post_refusing_get(monkeypatch, status=200, capture=cap)

    assert verify_provider_key(name, api_key="ark-key") == {"ok": True}
    assert cap["url"] == base_url + "/responses"
    assert cap["headers"]["Authorization"] == "Bearer ark-key"
    assert cap["json"] == {
        "model": model,
        "input": "Reply with OK.",
        "max_output_tokens": 1,
        "store": False,
    }


def test_verify_ark_profile_endpoint_override(monkeypatch):
    cap: dict = {}
    _patch_post_refusing_get(monkeypatch, status=200, capture=cap)

    verify_provider_key(
        "ark",
        api_key="ark-key",
        base_url="https://gateway.example/ark/v3/",
    )

    assert cap["url"] == "https://gateway.example/ark/v3/responses"


def test_verify_nvidia_self_hosted_lists_models(monkeypatch):
    """The descriptor's help text promises a self-hosted NIM container works. Such a
    container serves one model of its own, so the hosted probe's recommended id would
    404 there; with any endpoint other than the default, Test lists models instead
    (behind the key on a self-hosted NIM) and never POSTs a completion."""
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)

    def refuse_post(*_a, **_kw):
        raise AssertionError("a self-hosted NIM is probed with GET /models, not a completion")

    monkeypatch.setattr("httpx.post", refuse_post)
    assert verify_provider_key(
        "nvidia", api_key="nvapi-x", base_url="http://localhost:8000/v1/"
    ) == {"ok": True}
    assert cap["url"] == "http://localhost:8000/v1/models"
    assert cap["headers"]["Authorization"] == "Bearer nvapi-x"


def test_verify_nvidia_self_hosted_rejected_key_is_invalid(monkeypatch):
    _patch_get(monkeypatch, status=401)
    assert verify_provider_key(
        "nvidia", api_key="nvapi-bad", base_url="http://nim.internal:8000/v1"
    ) == {"ok": False, "error": "Invalid API key."}


def test_verify_network_error_is_clean(monkeypatch):
    _patch_get(monkeypatch, raise_exc=ConnectionError("boom"))
    res = verify_provider_key("openai", api_key="sk-x")
    assert res["ok"] is False
    assert "Couldn't reach" in res["error"]


def test_verify_unexpected_status(monkeypatch):
    _patch_get(monkeypatch, status=500)
    res = verify_provider_key("anthropic", api_key="sk-ant-x")
    assert res["ok"] is False
    assert "500" in res["error"]


# -- verify_provider_key: transport failures carry their cause (#505) ------------
def test_verify_transport_failure_includes_cause_and_hint(monkeypatch):
    import httpx

    _patch_get(
        monkeypatch,
        raise_exc=httpx.ConnectError(
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: "
            "unable to get local issuer certificate (_ssl.c:1000)"
        ),
    )
    result = verify_provider_key("openai", api_key="sk-x")
    assert result["ok"] is False
    assert "CERTIFICATE_VERIFY_FAILED" in result["error"]
    assert "CA bundle" in result["error"]


def test_verify_dns_failure_names_dns(monkeypatch):
    import httpx

    _patch_get(monkeypatch, raise_exc=httpx.ConnectError("[Errno -3] nodename nor servname provided"))
    error = verify_provider_key("openai", api_key="sk-x")["error"]
    assert "DNS" in error
    assert "[Errno -3]" in error


def test_verify_timeout_says_timed_out(monkeypatch):
    import httpx

    _patch_get(monkeypatch, raise_exc=httpx.ConnectTimeout("The request timed out."))
    error = verify_provider_key("openai", api_key="sk-x")["error"]
    assert "timed out" in error


def test_verify_silent_exception_falls_back_to_class_name_only(monkeypatch):
    class Weird(Exception):
        pass

    _patch_get(monkeypatch, raise_exc=Weird())
    assert verify_provider_key("openai", api_key="sk-x") == {"ok": False, "error": "Couldn't reach OpenAI (Weird)."}


# -- verify_provider_key: Azure OpenAI probes the user's resource, never api.openai.com ----
def test_verify_azure_lists_models_at_the_resource_v1_api(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    assert verify_provider_key(
        "azure", api_key="az-key", fields={"endpoint": "https://contoso.openai.azure.com"}
    ) == {"ok": True}
    assert cap["url"] == "https://contoso.openai.azure.com/openai/v1/models"
    assert cap["headers"]["Authorization"] == "Bearer az-key"
    # One probe: /openai/v1 is always normalised in, so no completions-route check follows.
    assert [c["url"] for c in cap["calls"]] == ["https://contoso.openai.azure.com/openai/v1/models"]
    assert not any("api.openai.com" in c["url"] for c in cap["calls"])


def test_verify_azure_accepts_an_endpoint_already_on_v1(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    verify_provider_key(
        "azure", api_key="az-key", fields={"endpoint": "https://contoso.openai.azure.com/openai/v1/"}
    )
    assert cap["url"] == "https://contoso.openai.azure.com/openai/v1/models"


def test_verify_azure_without_an_endpoint_asks_for_one_without_a_request(monkeypatch):
    cap: dict = {}
    _patch_get(monkeypatch, status=200, capture=cap)
    res = verify_provider_key("azure", api_key="az-key", fields={})
    assert res["ok"] is False and "endpoint" in res["error"].lower()
    assert "calls" not in cap


def test_verify_azure_bad_key_and_wrong_host_read_differently(monkeypatch):
    _patch_get(monkeypatch, status=401)
    assert verify_provider_key(
        "azure", api_key="az-bad", fields={"endpoint": "https://contoso.openai.azure.com"}
    ) == {"ok": False, "error": "Invalid API key."}
    _patch_get(monkeypatch, status=404)
    res = verify_provider_key(
        "azure", api_key="az-key", fields={"endpoint": "https://example.com"}
    )
    assert res["ok"] is False and "no Azure OpenAI v1 API" in res["error"]
