"""AWS Bedrock provider — client construction, auth paths, routing, registry, capabilities,
complete() and stream() delegation. SDK-free: the AnthropicBedrock client is monkeypatched so
no real AWS credentials or network calls are needed."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from coworker.providers import (
    AssistantTurn,
    ModelCapabilities,
    ProviderClient,
    ProviderRouter,
    StreamChunk,
    capabilities_for,
)
from coworker.providers.registry import (
    build_provider_client,
    get_descriptor,
    provider_names,
)


# -- fakes (same pattern as test_anthropic_provider.py) ----------------------------


class _FakeClient:
    """Records kwargs passed to messages.create and returns a canned response."""

    def __init__(self, response=None, events=None):
        self.kwargs: dict = {}

        def create(**kwargs):
            self.kwargs = kwargs
            if kwargs.get("stream"):
                return iter(events or [])
            return response

        self.messages = SimpleNamespace(create=create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=create))


def _text_response(text="hello from bedrock", stop_reason="end_turn"):
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
    )



# Helper: a fake boto3 session that returns no credentials (for tests that mock AnthropicBedrock)
class _FakeBoto3Session:
    def __init__(self, **kwargs):
        pass
    def get_credentials(self):
        return None

def _patch_boto3(monkeypatch):
    """Patch boto3.Session so _build_client falls back to aws_profile path."""
    import boto3
    monkeypatch.setattr(boto3, "Session", _FakeBoto3Session)


# -- BedrockProvider construction ---------------------------------------------------


class TestBedrockProviderConstruction:
    """Test that BedrockProvider can be built with various auth configurations."""

    def test_builds_with_aws_profile(self, monkeypatch):
        """Provider accepts aws_profile and aws_region."""
        from coworker.providers.bedrock_provider import BedrockProvider

        provider = BedrockProvider(aws_profile="my-sso-profile", aws_region="us-west-2")
        assert provider._aws_profile == "my-sso-profile"
        assert provider._aws_region == "us-west-2"

    def test_builds_with_explicit_credentials(self, monkeypatch):
        """Provider accepts explicit access key + secret key."""
        from coworker.providers.bedrock_provider import BedrockProvider

        provider = BedrockProvider(
            aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
            aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            aws_region="eu-west-1",
        )
        assert provider._aws_access_key_id == "AKIAIOSFODNN7EXAMPLE"
        assert provider._aws_secret_access_key == "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
        assert provider._aws_region == "eu-west-1"

    def test_defaults_to_us_east_1(self):
        """Region defaults to us-east-1 when not specified."""
        from coworker.providers.bedrock_provider import BedrockProvider

        provider = BedrockProvider()
        assert provider._aws_region == "us-east-1"

    def test_ensure_client_creates_anthropic_bedrock(self, monkeypatch):
        """_ensure_client() creates an AnthropicBedrock client with the correct params."""
        from coworker.providers.bedrock_provider import BedrockProvider

        captured: dict = {}

        class FakeAnthropicBedrock:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(
            "coworker.providers.bedrock_provider.AnthropicBedrock", FakeAnthropicBedrock
        )
        _patch_boto3(monkeypatch)

        provider = BedrockProvider(aws_profile="dev", aws_region="us-west-2")
        provider._ensure_client()

        assert captured["aws_region"] == "us-west-2"
        assert captured["aws_profile"] == "dev"

    def test_ensure_client_with_explicit_keys(self, monkeypatch):
        """_ensure_client() passes access key and secret key when provided."""
        from coworker.providers.bedrock_provider import BedrockProvider

        captured: dict = {}

        class FakeAnthropicBedrock:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(
            "coworker.providers.bedrock_provider.AnthropicBedrock", FakeAnthropicBedrock
        )

        provider = BedrockProvider(
            aws_access_key_id="AKIA123",
            aws_secret_access_key="secret456",
            aws_region="ap-southeast-1",
        )
        provider._ensure_client()

        assert captured["aws_region"] == "ap-southeast-1"
        # SDK uses aws_access_key / aws_secret_key (not boto3-style names)
        assert captured["aws_access_key"] == "AKIA123"
        assert captured["aws_secret_key"] == "secret456"

    def test_ensure_client_omits_none_profile(self, monkeypatch):
        """When profile is None, it should not be passed (use default credential chain)."""
        from coworker.providers.bedrock_provider import BedrockProvider

        captured: dict = {}

        class FakeAnthropicBedrock:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(
            "coworker.providers.bedrock_provider.AnthropicBedrock", FakeAnthropicBedrock
        )
        _patch_boto3(monkeypatch)

        provider = BedrockProvider(aws_region="us-east-1")
        provider._ensure_client()

        assert captured["aws_region"] == "us-east-1"
        # No profile set → falls back to aws_profile=None in kwargs
        assert captured.get("aws_profile") is None

    def test_client_is_cached(self, monkeypatch):
        """_ensure_client() builds once, then returns the cached client."""
        from coworker.providers.bedrock_provider import BedrockProvider

        call_count = 0

        class FakeAnthropicBedrock:
            def __init__(self, **kwargs):
                nonlocal call_count
                call_count += 1

        monkeypatch.setattr(
            "coworker.providers.bedrock_provider.AnthropicBedrock", FakeAnthropicBedrock
        )
        _patch_boto3(monkeypatch)

        provider = BedrockProvider(aws_profile="dev", aws_region="us-east-1")
        provider._ensure_client()
        provider._ensure_client()
        assert call_count == 1

    def test_ensure_client_passes_session_token(self, monkeypatch):
        """Explicit temporary credentials (access key + secret + session token) all reach
        the SDK — temp creds from SSO/assumed roles require the session token."""
        from coworker.providers.bedrock_provider import BedrockProvider

        captured: dict = {}

        class FakeAnthropicBedrock:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(
            "coworker.providers.bedrock_provider.AnthropicBedrock", FakeAnthropicBedrock
        )

        provider = BedrockProvider(
            aws_access_key_id="ASIA123",
            aws_secret_access_key="secret456",
            aws_session_token="token789",
            aws_region="us-east-1",
        )
        provider._ensure_client()

        assert captured["aws_access_key"] == "ASIA123"
        assert captured["aws_secret_key"] == "secret456"
        assert captured["aws_session_token"] == "token789"

    def test_ensure_client_profile_omitted_when_explicit_keys_given(self, monkeypatch):
        """Auth modes are mutually exclusive: explicit keys win, and the profile is NOT
        also handed to the SDK (which would make precedence SDK-dependent)."""
        from coworker.providers.bedrock_provider import BedrockProvider

        captured: dict = {}

        class FakeAnthropicBedrock:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setattr(
            "coworker.providers.bedrock_provider.AnthropicBedrock", FakeAnthropicBedrock
        )

        provider = BedrockProvider(
            aws_profile="dev",
            aws_access_key_id="AKIA123",
            aws_secret_access_key="secret456",
            aws_region="us-east-1",
        )
        provider._ensure_client()

        assert captured["aws_access_key"] == "AKIA123"
        # Profile must not be passed alongside explicit keys.
        assert captured.get("aws_profile") is None


# -- complete() and stream() delegation -------------------------------------------


class TestBedrockComplete:
    """Test that complete() and stream() work end-to-end via the Anthropic Messages API."""

    def test_complete_text_response(self):
        """complete() returns an AssistantTurn with text from a Bedrock-hosted Claude model."""
        from coworker.providers.bedrock_provider import BedrockProvider

        fake = _FakeClient(response=_text_response("hello from bedrock"))
        provider = BedrockProvider(aws_profile="dev", aws_region="us-east-1")
        provider._client = fake  # inject fake directly

        turn = provider.complete(
            model="us.anthropic.claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hi"}],
        )

        assert turn.text == "hello from bedrock"
        assert turn.finish_reason == "stop"
        assert not turn.has_tool_calls
        assert fake.kwargs["model"] == "us.anthropic.claude-sonnet-4-6"

    def test_complete_tool_use(self):
        """complete() parses tool_use blocks from Bedrock response."""
        from coworker.providers.bedrock_provider import BedrockProvider

        response = SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="let me check"),
                SimpleNamespace(
                    type="tool_use", id="c1", name="get_weather", input={"city": "NYC"}
                ),
            ],
            stop_reason="tool_use",
        )
        fake = _FakeClient(response=response)
        provider = BedrockProvider(aws_profile="dev", aws_region="us-east-1")
        provider._client = fake

        turn = provider.complete(
            model="us.anthropic.claude-sonnet-4-6",
            messages=[{"role": "user", "content": "weather?"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "get_weather",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )

        assert turn.text == "let me check"
        assert turn.finish_reason == "tool_calls"
        assert turn.tool_calls[0].name == "get_weather"
        assert turn.tool_calls[0].arguments == {"city": "NYC"}

    def test_stream_text_deltas(self):
        """stream() yields text deltas and a final turn."""
        from coworker.providers.bedrock_provider import BedrockProvider

        def _delta(index, **delta_attrs):
            return SimpleNamespace(
                type="content_block_delta",
                index=index,
                delta=SimpleNamespace(**delta_attrs),
            )

        events = [
            SimpleNamespace(
                type="content_block_start",
                index=0,
                content_block=SimpleNamespace(type="text"),
            ),
            _delta(0, type="text_delta", text="hel"),
            _delta(0, type="text_delta", text="lo"),
            SimpleNamespace(type="content_block_stop", index=0),
            SimpleNamespace(
                type="message_delta", delta=SimpleNamespace(stop_reason="end_turn")
            ),
            SimpleNamespace(type="message_stop"),
        ]
        fake = _FakeClient(events=events)
        provider = BedrockProvider(aws_profile="dev", aws_region="us-east-1")
        provider._client = fake

        chunks = list(
            provider.stream(
                model="us.anthropic.claude-sonnet-4-6",
                messages=[{"role": "user", "content": "hi"}],
            )
        )

        assert [c.text_delta for c in chunks if c.text_delta] == ["hel", "lo"]
        final = chunks[-1].turn
        assert final.text == "hello" and final.finish_reason == "stop"


# -- registry integration ----------------------------------------------------------


class TestBedrockRegistry:
    """Test that Bedrock is registered as a provider and can be built."""

    def test_bedrock_in_provider_names(self):
        """'bedrock' appears in the list of registered provider names."""
        assert "bedrock" in provider_names()

    def test_bedrock_descriptor_exists(self):
        """get_descriptor returns a valid descriptor for 'bedrock'."""
        desc = get_descriptor("bedrock")
        assert desc is not None
        assert desc.name == "bedrock"
        assert desc.needs_key is False  # IAM auth, not an API key

    def test_bedrock_descriptor_has_expected_fields(self):
        """Bedrock descriptor has fields for aws_profile, aws_region, and explicit keys."""
        desc = get_descriptor("bedrock")
        field_keys = [f.key for f in desc.fields]
        assert "aws_profile" in field_keys
        assert "aws_region" in field_keys
        assert "aws_access_key_id" in field_keys
        assert "aws_secret_access_key" in field_keys

    def test_bedrock_descriptor_secret_fields_are_marked(self):
        """Secret key field is marked as secret; profile/region are not."""
        desc = get_descriptor("bedrock")
        fields_by_key = {f.key: f for f in desc.fields}
        assert fields_by_key["aws_secret_access_key"].secret is True
        assert fields_by_key["aws_access_key_id"].secret is False
        assert fields_by_key["aws_profile"].secret is False
        assert fields_by_key["aws_region"].secret is False

    def test_build_provider_client_returns_bedrock_provider(self, monkeypatch):
        """build_provider_client('bedrock', ...) returns a BedrockProvider."""
        from coworker.providers.bedrock_provider import BedrockProvider

        provider = build_provider_client(
            "bedrock",
            {"aws_profile": "prod", "aws_region": "eu-west-1"},
            secrets=None,
        )
        assert isinstance(provider, BedrockProvider)
        assert provider._aws_profile == "prod"
        assert provider._aws_region == "eu-west-1"

    def test_build_with_explicit_keys(self, monkeypatch):
        """build_provider_client passes explicit AWS credentials through."""
        from coworker.providers.bedrock_provider import BedrockProvider

        provider = build_provider_client(
            "bedrock",
            {
                "aws_access_key_id": "AKIA123",
                "aws_secret_access_key": "secret",
                "aws_region": "us-west-2",
            },
            secrets=None,
        )
        assert isinstance(provider, BedrockProvider)
        assert provider._aws_access_key_id == "AKIA123"
        assert provider._aws_secret_access_key == "secret"

    def test_build_defaults_region(self):
        """Empty/missing region defaults to us-east-1."""
        from coworker.providers.bedrock_provider import BedrockProvider

        provider = build_provider_client("bedrock", {}, secrets=None)
        assert isinstance(provider, BedrockProvider)
        assert provider._aws_region == "us-east-1"


# -- ProviderRouter integration ---------------------------------------------------


class TestBedrockRouting:
    """Test that the ProviderRouter correctly routes bedrock: prefixed models."""

    def test_router_recognizes_bedrock_prefix(self):
        """bedrock:model-id routes to the bedrock provider."""
        router = ProviderRouter(secrets=None)
        assert router._provider_name("bedrock:us.anthropic.claude-sonnet-4-6") == "bedrock"

    def test_router_strips_bedrock_prefix(self):
        """The router strips `bedrock:` prefix, leaving the Bedrock model ID intact."""
        router = ProviderRouter(secrets=None)
        bare = router._bare("bedrock:us.anthropic.claude-sonnet-4-6")
        assert bare == "us.anthropic.claude-sonnet-4-6"

    def test_router_dispatches_to_bedrock_provider(self, monkeypatch):
        """A bedrock:-prefixed model call reaches the Bedrock provider client."""
        from coworker.providers.bedrock_provider import BedrockProvider

        captured_model: list[str] = []

        class FakeBedrockProvider(ProviderClient):
            def complete(self, *, model, messages, tools=None, **settings):
                captured_model.append(model)
                return AssistantTurn(text="bedrock response")

            def stream(self, *, model, messages, tools=None, **settings):
                yield StreamChunk(turn=AssistantTurn(text="bedrock response"))

            def capabilities(self, model):
                return ModelCapabilities()

        def fake_build(name, profile, secrets):
            if name == "bedrock":
                return FakeBedrockProvider()
            from coworker.providers.openai_provider import OpenAIProvider

            return OpenAIProvider(api_key="test")

        monkeypatch.setattr("coworker.providers.router.build_provider_client", fake_build)

        router = ProviderRouter(secrets=None)
        turn = router.complete(
            model="bedrock:us.anthropic.claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert turn.text == "bedrock response"
        assert captured_model == ["us.anthropic.claude-sonnet-4-6"]


# -- capabilities -----------------------------------------------------------------


class TestBedrockCapabilities:
    """Test capability flags for Bedrock models."""

    def test_curated_bedrock_model_has_vision_and_tools(self):
        """Matrix-listed Bedrock models have full agentic capabilities."""
        caps = capabilities_for("bedrock:us.anthropic.claude-sonnet-4-6")
        assert caps.tools is True
        assert caps.vision is True
        assert caps.streaming is True
        assert caps.parallel_tool_calls is True

    def test_uncurated_bedrock_model_gets_heuristic_caps(self):
        """An arbitrary bedrock: model not in the matrix falls to heuristics."""
        caps = capabilities_for("bedrock:some-custom-model")
        # Should still get reasonable defaults (tools + streaming at minimum)
        assert caps.tools is True
        assert caps.streaming is True


# -- verify_provider_key -----------------------------------------------------------


class TestBedrockVerify:
    """Test the verification path for Bedrock credentials."""

    def test_verify_bedrock_uses_sts_get_caller_identity(self, monkeypatch):
        """Bedrock verification calls STS GetCallerIdentity to validate credentials."""
        from coworker.providers.registry import verify_provider_key

        captured: dict = {}

        class FakeSession:
            def __init__(self, **kwargs):
                captured["session_kwargs"] = kwargs

            def client(self, service):
                captured["service"] = service

                class FakeSTS:
                    def get_caller_identity(self):
                        return {"Account": "123456789012"}

                return FakeSTS()

        import boto3
        monkeypatch.setattr(boto3, "Session", FakeSession)

        result = verify_provider_key(
            "bedrock",
            api_key=None,
            base_url=None,
        )
        assert result["ok"] is True

    def test_verify_bedrock_with_profile(self, monkeypatch):
        """Verification passes the entered profile through to boto3.Session — the fields the
        user typed must actually be tested, not the ambient default chain."""
        from coworker.providers.registry import verify_provider_key

        captured: dict = {}

        class FakeSession:
            def __init__(self, **kwargs):
                captured["session_kwargs"] = kwargs

            def client(self, service, **kwargs):
                class FakeSTS:
                    def get_caller_identity(self):
                        return {"Account": "123456789012"}

                return FakeSTS()

        import boto3
        monkeypatch.setattr(boto3, "Session", FakeSession)

        result = verify_provider_key(
            "bedrock", fields={"aws_profile": "my-dev-profile", "aws_region": "us-west-2"}
        )
        assert result["ok"] is True
        assert captured["session_kwargs"].get("profile_name") == "my-dev-profile"

    def test_verify_bedrock_with_explicit_keys(self, monkeypatch):
        """Explicit access key + secret + session token entered in the form are the creds
        actually validated, not whatever ambient credentials exist."""
        from coworker.providers.registry import verify_provider_key

        captured: dict = {}

        class FakeSession:
            def __init__(self, **kwargs):
                captured["session_kwargs"] = kwargs

            def client(self, service, **kwargs):
                class FakeSTS:
                    def get_caller_identity(self):
                        return {"Account": "123456789012"}

                return FakeSTS()

        import boto3
        monkeypatch.setattr(boto3, "Session", FakeSession)

        result = verify_provider_key(
            "bedrock",
            fields={
                "aws_access_key_id": "ASIA123",
                "aws_secret_access_key": "secret456",
                "aws_session_token": "token789",
            },
        )
        assert result["ok"] is True
        assert captured["session_kwargs"].get("aws_access_key_id") == "ASIA123"
        assert captured["session_kwargs"].get("aws_secret_access_key") == "secret456"
        assert captured["session_kwargs"].get("aws_session_token") == "token789"

    def test_verify_bedrock_invalid_credentials(self, monkeypatch):
        """Invalid AWS credentials produce a clean error message."""
        from coworker.providers.registry import verify_provider_key

        class FakeSession:
            def __init__(self, **kwargs):
                pass

            def client(self, service):
                class FakeSTS:
                    def get_caller_identity(self):
                        raise Exception("InvalidClientTokenId")

                return FakeSTS()

        import boto3
        monkeypatch.setattr(boto3, "Session", FakeSession)

        result = verify_provider_key("bedrock")
        assert result["ok"] is False
        assert "AWS" in result["error"] or "credentials" in result["error"].lower()

    def test_verify_bedrock_network_error(self, monkeypatch):
        """Network errors produce a clean error message."""
        from coworker.providers.registry import verify_provider_key

        class FakeSession:
            def __init__(self, **kwargs):
                raise ConnectionError("no network")

        import boto3
        monkeypatch.setattr(boto3, "Session", FakeSession)

        result = verify_provider_key("bedrock")
        assert result["ok"] is False
        assert "Couldn't reach" in result["error"] or result["error"]


# -- manager integration -----------------------------------------------------------


class TestBedrockManagerIntegration:
    """Test Bedrock works with the SessionManager's provider config flow."""

    def test_set_provider_bedrock_marks_configured(self, tmp_path, monkeypatch):
        """set_provider('bedrock', {profile, region}) marks it configured (no key needed)."""
        monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
        from coworker.server.manager import SessionManager

        mgr = SessionManager(data_dir=tmp_path)
        res = mgr.set_provider("bedrock", {"aws_profile": "dev", "aws_region": "us-west-2"})
        assert res["ok"] is True

        provs = {p["name"]: p for p in mgr.get_providers()}
        assert provs["bedrock"]["configured"] is True
        assert provs["bedrock"]["needs_key"] is False

    def test_bedrock_models_appear_when_configured(self, tmp_path, monkeypatch):
        """Once Bedrock is configured, its matrix models appear in settings."""
        monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
        from coworker.server.manager import SessionManager

        mgr = SessionManager(data_dir=tmp_path)
        mgr.set_provider("bedrock", {"aws_profile": "dev", "aws_region": "us-east-1"})
        models = mgr.get_settings()["models"]
        # At least one bedrock model should appear
        bedrock_models = [m for m in models if m.startswith("bedrock:")]
        assert len(bedrock_models) > 0

    def test_bedrock_models_appear_with_default_credential_chain(
        self, tmp_path, monkeypatch
    ):
        """Bedrock's documented auth mode 3 is the default credential chain (IAM role /
        env vars) with ALL fields left blank. Configuring it that way stores an empty
        profile, and its models must still surface — hiding them strands IAM-role users.

        The default model is always force-inserted into the selectable list, so this test
        moves the default away from Bedrock and checks a *non-default* Bedrock model, which
        only appears if the selectable gate itself accepts the empty-profile config."""
        monkeypatch.setenv("COWORKER_STATE_DIR", str(tmp_path / "state"))
        from coworker.server.manager import SessionManager

        mgr = SessionManager(data_dir=tmp_path)
        res = mgr.set_provider("bedrock", {})  # all optional fields blank
        assert res["ok"] is True
        # Take the default off Bedrock so it isn't force-inserted, then add a second
        # Bedrock model whose visibility is decided purely by the selectable gate.
        mgr.set_default_model("gpt-5.6-sol")
        mgr.add_model("bedrock:us.anthropic.claude-opus-4-8")

        models = mgr.get_settings()["models"]
        bedrock_models = [m for m in models if m.startswith("bedrock:")]
        assert "bedrock:us.anthropic.claude-opus-4-8" in bedrock_models


# -- matrix entries ----------------------------------------------------------------


class TestBedrockMatrix:
    """Test that Bedrock models are in the curated matrix."""

    def test_bedrock_opus_in_matrix(self):
        """Bedrock Claude Opus 4.8 is in the curated matrix."""
        from coworker.providers.matrix import entry_for

        entry = entry_for("bedrock:us.anthropic.claude-opus-4-8")
        assert entry is not None
        assert "Opus" in entry.label
        assert "Bedrock" in entry.label

    def test_bedrock_haiku_in_matrix(self):
        """Bedrock Claude Haiku is in the curated matrix."""
        from coworker.providers.matrix import entry_for

        entry = entry_for("bedrock:us.anthropic.claude-haiku-4-5-20251001-v1:0")
        assert entry is not None
        assert "Haiku" in entry.label
        assert "Bedrock" in entry.label

    def test_models_for_provider_bedrock(self):
        """models_for_provider('bedrock') returns the bare model ids."""
        from coworker.providers.matrix import models_for_provider

        models = models_for_provider("bedrock")
        assert len(models) >= 2
        assert all(not m.startswith("bedrock:") for m in models)  # prefix stripped


# -- dependency declaration --------------------------------------------------------


class TestBedrockDependency:
    """boto3 must be a declared runtime dependency — AnthropicBedrock and the verify
    path both import it, so a fresh install without it crashes on first Bedrock use."""

    def test_boto3_is_declared(self):
        import tomllib
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        deps = " ".join(data["project"]["dependencies"]).lower()
        # Either an explicit boto3 pin or the anthropic[bedrock] extra satisfies this.
        assert "boto3" in deps or "anthropic[bedrock]" in deps


# -- thinking config ---------------------------------------------------------------


# -- credential expiry retry ---------------------------------------------------


class TestBedrockCredentialRetry:
    """Test that expired credentials trigger a client rebuild and retry."""

    def test_complete_shows_error_on_expired_token(self, monkeypatch):
        """A 403/expired error rebuilds with fresh boto3 creds; if that also fails,
        raises a friendly message."""
        from coworker.providers.bedrock_provider import BedrockProvider

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            @property
            def messages(self):
                from types import SimpleNamespace

                def create(**kwargs):
                    raise Exception(
                        "Error code: 403 - {'message': 'The security token included in the request is expired'}"
                    )

                return SimpleNamespace(create=create)

            @property
            def beta(self):
                return SimpleNamespace(messages=self.messages)

        monkeypatch.setattr(
            "coworker.providers.bedrock_provider.AnthropicBedrock", FakeClient
        )

        # Mock boto3.Session for the fresh-creds path
        from types import SimpleNamespace

        class FakeFrozen:
            access_key = "AKIA_FRESH"
            secret_key = "secret_fresh"
            token = "token_fresh"

        class FakeCreds:
            def get_frozen_credentials(self):
                return FakeFrozen()

        class FakeSession:
            def __init__(self, **kwargs):
                pass

            def get_credentials(self):
                return FakeCreds()

        import boto3
        monkeypatch.setattr(boto3, "Session", FakeSession)

        provider = BedrockProvider(aws_profile="dev", aws_region="us-east-1")
        with pytest.raises(RuntimeError, match="re-authenticate"):
            provider.complete(
                model="us.anthropic.claude-sonnet-4-6",
                messages=[{"role": "user", "content": "hi"}],
            )
        # After both attempts fail, client is invalidated for next retry
        assert provider._client is None

    def test_next_call_after_expiry_rebuilds_client(self, monkeypatch):
        """After expiry error, the retry within the same call picks up fresh creds
        from boto3 and succeeds."""
        from coworker.providers.bedrock_provider import BedrockProvider
        from types import SimpleNamespace

        call_count = 0

        class FakeClient:
            def __init__(self, **kwargs):
                self._kwargs = kwargs

            @property
            def messages(self):
                def create(**kwargs):
                    nonlocal call_count
                    call_count += 1
                    if call_count == 1:
                        raise Exception("403 - security token expired")
                    return SimpleNamespace(
                        content=[SimpleNamespace(type="text", text="success after refresh")],
                        stop_reason="end_turn",
                    )

                return SimpleNamespace(create=create)

            @property
            def beta(self):
                return SimpleNamespace(messages=self.messages)

        monkeypatch.setattr(
            "coworker.providers.bedrock_provider.AnthropicBedrock", FakeClient
        )

        class FakeFrozen:
            access_key = "AKIA_FRESH"
            secret_key = "secret_fresh"
            token = "token_fresh"

        class FakeCreds:
            def get_frozen_credentials(self):
                return FakeFrozen()

        class FakeSession:
            def __init__(self, **kwargs):
                pass

            def get_credentials(self):
                return FakeCreds()

        import boto3
        monkeypatch.setattr(boto3, "Session", FakeSession)

        provider = BedrockProvider(aws_profile="dev", aws_region="us-east-1")

        # First call: expires → rebuilds with fresh boto3 creds → second attempt succeeds
        turn = provider.complete(
            model="us.anthropic.claude-sonnet-4-6",
            messages=[{"role": "user", "content": "hi"}],
        )
        assert turn.text == "success after refresh"
        assert call_count == 2

    def test_non_expired_errors_propagate_immediately(self, monkeypatch):
        """Non-credential errors are NOT retried."""
        from coworker.providers.bedrock_provider import BedrockProvider

        class FakeClient:
            def __init__(self, **kwargs):
                pass

            @property
            def messages(self):
                from types import SimpleNamespace

                def create(**kwargs):
                    raise Exception("ValidationException: model not found")

                return SimpleNamespace(create=create)

            @property
            def beta(self):
                return SimpleNamespace(messages=self.messages)

        monkeypatch.setattr(
            "coworker.providers.bedrock_provider.AnthropicBedrock", FakeClient
        )
        _patch_boto3(monkeypatch)

        provider = BedrockProvider(aws_profile="dev", aws_region="us-east-1")
        with pytest.raises(Exception, match="model not found"):
            provider.complete(
                model="us.anthropic.claude-sonnet-4-6",
                messages=[{"role": "user", "content": "hi"}],
            )


class TestBedrockThinking:
    """Bedrock Claude ids arrive bare as `us.anthropic.claude-...`. The thinking-config
    heuristic must recognize them so a thinking_budget override produces the right shape."""

    def test_budget_thinking_for_haiku_uses_enabled_not_adaptive(self):
        """Haiku 4.5 on Bedrock uses budget-style thinking. A thinking_budget override must
        emit {"type": "enabled", "budget_tokens": N}, never adaptive (which Bedrock rejects)."""
        from coworker.providers.bedrock_provider import BedrockProvider

        provider = BedrockProvider(aws_region="us-east-1", thinking_budget=8192)
        kwargs = provider._request_kwargs(
            model="us.anthropic.claude-haiku-4-5-20251001-v1:0",
            messages=[{"role": "user", "content": "hi"}],
            tools=None,
            settings={},
        )
        assert kwargs["thinking"]["type"] == "enabled"
        assert kwargs["thinking"]["budget_tokens"] == 8192
