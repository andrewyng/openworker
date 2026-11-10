"""The models a provider serves right now, asked with the user's own key (OPE-252, layer 1).

The curated catalog (``model_catalog.json``) says what we vouch for; this module says what the
user's key can actually call today, straight from the provider. Nothing here talks to anyone
but the provider the user configured, and nothing is cached across installs: the manager
keeps one small JSON file per machine with the last listing per provider, so "new since last
check" and "not available on this key" can be answered without a request.

Every provider's list is read by its own reader (``_READERS``), because no two lists agree
on what a row says. A reader turns each row into a ``LiveModel`` with a *kind* and where that
kind came from:

* ``provider``: a field of the provider's own list said so (output modalities, a ``type`` or
  ``domain``, a ``capabilities`` block, an ``endpoints`` list, a visibility flag).
* ``name``: the provider gives nothing per row, so a short, provider-scoped word list decided
  (``whisper`` on OpenAI, ``muse-image`` on Meta). Each list is justified by the rows that
  provider actually returned on 2026-11-09 and pinned by a fixture test.
* ``none``: nothing decided; the row is offered as a chat model, which is the default.

Only ``chat`` rows are offered on the provider page; everything else is shown set aside with
the reason, never hidden, and any id (and alias) the provider listed stays in ``all_ids`` so a
typed id is never refused as "not on this key". Facts a list carries (window, output cap,
image or PDF input, tools, reasoning, retirement) ride along; a missing fact is ``None``.

Three providers still have no list the app can ask with what it holds: Bedrock (an AWS
API call signed per region), Vertex (per project and region behind a Google Cloud sign-in)
and the Ark agent plan (a signed control-plane action). They answer ``listing: none`` and the
page shows the curated rows.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from . import probe_http

LISTING_TIMEOUT = 8.0

# Providers whose model list cannot be asked for yet (and why, for the page).
NO_LISTING: dict[str, str] = {
    "bedrock": "Bedrock lists models per region with AWS credentials the app does not sign yet; the curated rows are shown.",
    "vertex": "Vertex AI lists models per project and region behind a Google Cloud sign-in the app does not do yet; the curated rows are shown.",
    "ark-agent-plan-cn": "The Ark agent plan has no model list on its data endpoint; the curated rows are shown.",
}

CODEX_MODELS_URL = "https://chatgpt.com/backend-api/codex/models"
COHERE_MODELS_URL = "https://api.cohere.com/v1/models"

CHAT = "chat"

# One phrase per kind, identical across providers (design §5). ``{detail}`` is the vendor's
# own word or the matched word; ``{date}`` the retirement date.
KIND_PHRASES: dict[str, str] = {
    "image": "Built for image generation",
    "video": "Built for video generation",
    "speech_to_text": "Built for speech to text",
    "text_to_speech": "Built for text to speech",
    "music": "Built for music generation",
    "embedding": "Built for embeddings",
    "rerank": "Built for reranking",
    "moderation": "Built for content moderation",
    "document": "Built for document parsing",
    "completion": "Raw completion model, no chat format",
    "live": "Built for live voice sessions",
    "specialised": "Specialised: {detail}",
    "hybrid_image": "Also generates images",
    "hybrid_audio": "Also generates speech",
    "retired": "Deprecated · ended {date}",
}


@dataclass(frozen=True)
class LiveModel:
    id: str
    label: str
    kind: str = CHAT
    kind_source: str = "none"  # provider | name | none
    detail: Optional[str] = None  # the matched word, or the vendor's own kind word
    context: Optional[int] = None  # the total window, as the vendor reports it
    max_output: Optional[int] = None  # a separate output cap, where one exists
    input_image: Optional[bool] = None
    input_pdf: Optional[bool] = None
    tools: Optional[bool] = None
    reasoning: Optional[bool] = None
    retires_at: Optional[str] = None  # ISO date
    created: Optional[str] = None  # ISO date, when the listing says
    aliases: tuple[str, ...] = ()  # other ids the provider accepts for this row

    @property
    def offered(self) -> bool:
        return self.kind == CHAT

    @property
    def reason_phrase(self) -> Optional[str]:
        """Why a row is set aside, in English (the GUI translates by kind). Hedged when only
        our name rule decided: the provider did not say."""
        if self.kind == CHAT:
            return None
        phrase = KIND_PHRASES.get(self.kind, "Not a chat model")
        phrase = phrase.format(detail=self.detail or "other tasks", date=self.retires_at or "an earlier date")
        if self.kind_source == "name":
            return "Likely " + phrase[0].lower() + phrase[1:]
        return phrase


@dataclass
class Listing:
    provider: str
    ok: bool
    models: list[LiveModel]  # every row the provider listed, chat or not
    error: Optional[str] = None
    status: Optional[int] = None
    # Every id the provider listed, and every alias: an image model is not offered as a
    # coworker's model, but a user who types its id is not told the key lacks it.
    all_ids: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.all_ids is None:
            self.all_ids = []


def as_dict(row: LiveModel) -> dict[str, Any]:
    out = asdict(row)
    out["aliases"] = list(row.aliases)
    out["reason"] = row.reason_phrase
    return out


# -- the request ---------------------------------------------------------------------------


def key_for(profile: dict[str, Any], env_key: Optional[str]) -> str:
    """The key a session would use: the stored one, else the provider's environment variable
    (the same order as resolve_api_key in every provider)."""
    stored = str((profile or {}).get("api_key") or "").strip()
    if stored:
        return stored
    return str(os.environ.get(env_key) or "").strip() if env_key else ""


def _default_base(descriptor: Any) -> str:
    return str(next((f.default for f in getattr(descriptor, "fields", []) if f.key == "base_url" and f.default), "")).rstrip("/")


def listing_request(name: str, profile: dict[str, Any], descriptor: Any) -> Optional[dict[str, Any]]:
    """url / headers / params for a provider's model list, or None when it has none. A
    provider whose facts need a second call carries it under ``extra``."""
    if name in NO_LISTING or descriptor is None or getattr(descriptor, "kind", "") == "local":
        return None
    profile = profile or {}
    key = key_for(profile, getattr(descriptor, "env_key", None))
    bearer = {"Authorization": f"Bearer {key}"} if key else {}
    if name == "anthropic":
        return {
            "url": "https://api.anthropic.com/v1/models",
            "headers": {"x-api-key": key, "anthropic-version": "2023-06-01"},
            "params": {"limit": "1000"},
        }
    if name == "gemini":
        return {
            "url": "https://generativelanguage.googleapis.com/v1beta/models",
            "headers": {},
            "params": {"key": key, "pageSize": "1000"},
        }
    if name == "openai-codex":
        # The ChatGPT subscription backend lists what the plan can run; the manager hands
        # over a live OAuth bearer and the account id (codex_auth), never a key.
        tokens = profile.get("tokens") if isinstance(profile.get("tokens"), dict) else {}
        access = str(profile.get("access_token") or tokens.get("access_token") or "").strip()
        headers = {"Authorization": f"Bearer {access}"} if access else {}
        account = str(profile.get("account_id") or "").strip()
        if account:
            headers["chatgpt-account-id"] = account
        headers["originator"] = "openworker"
        return {"url": CODEX_MODELS_URL, "headers": headers, "params": {"client_version": "99.0.0"}}
    if name == "azure":
        from .registry import normalize_azure_endpoint

        endpoint = normalize_azure_endpoint(profile.get("endpoint"))
        if not endpoint:
            return None
        return {"url": endpoint + "/models", "headers": bearer, "params": None}
    if name == "cohere":
        # The Compatibility API's list is bare; Cohere's native list says what each model is
        # for and takes an endpoint filter, so the page asks it whatever base URL is saved.
        return {"url": COHERE_MODELS_URL, "headers": bearer, "params": {"endpoint": "chat", "page_size": "1000"}}
    if name == "openrouter-account":
        return {"url": "https://openrouter.ai/api/v1/models/user", "headers": bearer, "params": None}
    base = str(profile.get("base_url") or "").strip().rstrip("/") or _default_base(descriptor)
    if not base:
        base = "https://api.openai.com/v1" if name == "openai" else ""
    if not base:
        return None
    if name == "xai":
        # ids, modalities and aliases live on /language-models; the window only on /models.
        return {"url": base + "/language-models", "headers": bearer, "params": None, "extra": {"url": base + "/models", "headers": bearer, "params": None}}
    params: Optional[dict[str, str]] = None
    if name == "siliconflow":
        params = {"sub_type": "chat"}  # the only kind signal SiliconFlow has is the query
    elif name == "nebius":
        params = {"verbose": "true"}  # the plain list is ids only
    return {"url": base + "/models", "headers": bearer, "params": params}


# -- small readers of a row ----------------------------------------------------------------


def _int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if value > 0 else None


def _get(item: dict[str, Any], path: str) -> Any:
    cur: Any = item
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _first_int(item: dict[str, Any], *paths: str) -> Optional[int]:
    for path in paths:
        got = _int(_get(item, path))
        if got:
            return got
    return None


def _bool(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _lst(item: dict[str, Any], path: str) -> list[Any]:
    value = _get(item, path)
    return value if isinstance(value, list) else []


def _str(value: Any) -> Optional[str]:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _date(value: Any) -> Optional[str]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value > 0:
        try:
            return time.strftime("%Y-%m-%d", time.gmtime(value))
        except (OverflowError, ValueError, OSError):
            return None
    if isinstance(value, str) and len(value) >= 10 and value[4] == "-" and value[7] == "-":
        return value[:10]
    return None


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _retirement(when: Any) -> tuple[Optional[str], bool]:
    """(ISO date, already past) for a vendor's retirement or shutdown field."""
    date = _date(when)
    return date, bool(date and date <= _today())


def _modality_kind(inputs: list[Any], outputs: list[Any]) -> Optional[str]:
    """What a row's input and output modalities say it is; None when the list gives none."""
    out = {str(m).lower() for m in outputs}
    inp = {str(m).lower() for m in inputs}
    if not out:
        return None
    if out == {"text"}:
        if inp and "text" not in inp and "audio" in inp:
            return "speech_to_text"
        return CHAT
    if "text" in out:
        if "image" in out or "video" in out:
            return "hybrid_image"
        if "audio" in out:
            return "hybrid_audio"
        return CHAT
    if "video" in out:
        return "video"
    if "image" in out:
        return "image"
    if "audio" in out or "speech" in out:
        return "text_to_speech"
    if any("transcri" in m for m in out):
        return "speech_to_text"  # Groq writes the output as "transcription"
    if any("embed" in m for m in out):
        return "embedding"
    return "specialised"


Rule = tuple[str, str, Optional[str]]  # (word in the id or label, kind, detail)


def _name_rule(model_id: str, label: str, rules: list[Rule]) -> Optional[tuple[str, str, Optional[str]]]:
    """(kind, matched word, detail) from a provider-scoped word list, first match wins."""
    low_id, low_label = model_id.lower(), label.lower()
    for word, kind, detail in rules:
        if word in low_id or word in low_label:
            return kind, word, detail or word
    return None


def _ident(item: dict[str, Any], *keys: str) -> Optional[str]:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _label(item: dict[str, Any], model_id: str, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip() and value.strip() != model_id:
            return value.strip()
    return model_id


_FIREWORKS_NAMESPACE = re.compile(r"^accounts/[^/]+/(?:models|routers)/")
_FIREWORKS_DOT = re.compile(r"(?<=\d)p(?=\d)")  # Fireworks writes 5.3 as 5p3
_ACRONYMS = {"glm", "gpt", "oss", "vl", "ai", "it"}
_BRANDS = {"deepseek": "DeepSeek", "minimax": "MiniMax", "kimi": "Kimi", "qwen": "Qwen", "llama": "Llama", "firerouter": "FireRouter"}
_SIZE = re.compile(r"^[a-z]?\d+(?:\.\d+)?[bt]$", re.I)  # 120b, 8B, a12b, 2.4t
# Bump when display_label() names rows differently: a cached listing keeps the labels it was
# read with, so the manager asks the provider again rather than show the old names.
LABELS_VERSION = 2
_O_SERIES = re.compile(r"^o\d$")  # OpenAI's o3, o4 stay lower case


def _cased(token: str) -> str:
    low = token.lower()
    if low in _ACRONYMS:
        return low.upper()
    if low in _BRANDS:
        return _BRANDS[low]
    if _SIZE.match(token):
        return token.upper()
    if _O_SERIES.match(low):
        return low
    return token[:1].upper() + token[1:]


def display_label(model_id: str) -> str:
    """A readable name for a row whose list gives none, for display only: the full id stays
    the row's id and shows beside the name. Drops the namespace (`accounts/<org>/models/`,
    `<org>/`), writes Fireworks' `5p3` as `5.3`, turns hyphens into spaces and cases each
    word: `accounts/fireworks/models/deepseek-v4p1-flash-1017` -> "DeepSeek V4.1 Flash 1017",
    `gpt-5.4-nano` -> "GPT-5.4 Nano", `gpt-oss-120b` -> "GPT-OSS 120B". A leading acronym keeps
    its hyphen to the version (GPT-5.4, GLM-5.3), and two acronyms stay one word (GPT-OSS). Tier words such as
    Fast or Spot stay in the name."""
    short = _FIREWORKS_NAMESPACE.sub("", model_id)
    if short != model_id:
        short = _FIREWORKS_DOT.sub(".", short)
    elif "/" in model_id:
        short = model_id.rsplit("/", 1)[-1]
    tokens = [t for t in re.split(r"[-_]", short) if t]
    if not tokens:
        return model_id
    out = _cased(tokens[0])
    for i, (prev, token) in enumerate(zip(tokens, tokens[1:])):
        acronym = prev.lower() in _ACRONYMS
        # the leading acronym keeps its version (GPT-5.4); two acronyms stay one word (GPT-OSS)
        joined = acronym and ((i == 0 and token[:1].isdigit()) or token.lower() in _ACRONYMS)
        out += ("-" if joined else " ") + _cased(token)
    return out


def _row(model_id: str, label: str, kind: Optional[str], source: str, detail: Optional[str] = None, **facts: Any) -> LiveModel:
    if kind is None or kind == CHAT:
        kind, source = CHAT, (source if source == "provider" else "none")
    if label == model_id:
        label = display_label(model_id)
    return LiveModel(id=model_id, label=label, kind=kind, kind_source=source, detail=detail, **facts)


def _with_name_rule(row: LiveModel, rules: list[Rule]) -> LiveModel:
    """A row the provider called chat (or said nothing about) checked against the
    provider's own word list; a match sets the row aside with the word as the reason."""
    if row.kind != CHAT:
        return row
    hit = _name_rule(row.id, row.label, rules)
    if hit is None:
        return row
    kind, word, detail = hit
    return LiveModel(**{**asdict(row), "kind": kind, "kind_source": "name", "detail": detail if kind == "specialised" else word})


def _fold(rows: list[LiveModel], base_of: Callable[[str], Optional[str]]) -> list[LiveModel]:
    """Dated twins (`claude-haiku-4-5-20251001`, `gpt-5-2025-08-07`) fold into the undated
    row when the list has it, else stay their own row with the undated id as an alias: the
    API accepts both and the catalog carries the undated one."""
    by_id = {r.id: r for r in rows}
    out: dict[str, LiveModel] = {}
    extra: dict[str, list[str]] = {}
    for row in rows:
        base = base_of(row.id)
        if base and base != row.id and base in by_id:
            extra.setdefault(base, []).append(row.id)
            continue
        if base and base != row.id:
            row = LiveModel(**{**asdict(row), "aliases": tuple(dict.fromkeys([*row.aliases, base]))})
        out[row.id] = row
    for base, dated in extra.items():
        row = out[base]
        out[base] = LiveModel(**{**asdict(row), "aliases": tuple(dict.fromkeys([*row.aliases, *dated]))})
    return list(out.values())


_ANTHROPIC_DATED = re.compile(r"-\d{8}$")
_OPENAI_DATED = re.compile(r"-\d{4}-\d{2}-\d{2}$")


# -- the readers, one per list shape --------------------------------------------------------
#
# Each takes the rows of the answer (and, for xAI, the rows of the second call) and returns
# every row as a LiveModel. Non-chat rows are kept with their kind; nothing is dropped.


def _read_openrouter(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        inputs, outputs = _lst(it, "architecture.input_modalities"), _lst(it, "architecture.output_modalities")
        params = _lst(it, "supported_parameters")
        endpoints = [str(e) for e in _lst(it, "endpoints")]
        if mid.startswith("openrouter/"):
            kind, source = CHAT, "provider"  # the router's own auto routes answer chat
        else:
            kind = _modality_kind(inputs, outputs)
            source = "provider" if kind else "none"
            if kind == CHAT and endpoints and not any(e.endswith(("/chat/completions", "/messages", "/responses")) for e in endpoints):
                kind = "completion"
        retires, past = _retirement(it.get("expiration_date"))
        if past:
            kind, source = "retired", "provider"
        rows.append(_row(
            mid, _label(it, mid, "name"), kind, source,
            context=_first_int(it, "context_length", "top_provider.context_length"),
            max_output=_first_int(it, "top_provider.max_completion_tokens"),
            input_image=("image" in inputs) if inputs else None,
            input_pdf=("file" in inputs) if inputs else None,
            tools=("tools" in params) if params else None,
            reasoning=(("reasoning" in params or "include_reasoning" in params) or isinstance(it.get("reasoning"), dict)) if (params or "reasoning" in it) else None,
            retires_at=retires,
            created=_date(it.get("created")),
        ))
    return rows


def _read_orcarouter(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        caps = [str(c) for c in _lst(it, "endpoint_capabilities")]
        if isinstance(it.get("architecture"), dict):
            inputs, outputs = _lst(it, "architecture.input_modalities"), _lst(it, "architecture.output_modalities")
            kind = _modality_kind(inputs, outputs)
            source = "provider" if kind else "none"
            if kind == CHAT and caps and not any(c in ("chat_completions", "anthropic_messages") for c in caps):
                kind = "completion"
        else:
            inputs, kind, source = [], None, "none"  # the router's own routes: bare rows
        params = _lst(it, "supported_parameters")
        rows.append(_row(
            mid, _label(it, mid, "name"), kind, source,
            context=_first_int(it, "context_length"),
            max_output=_first_int(it, "max_completion_tokens"),
            input_image=("image" in inputs) if inputs else None,
            tools=("tools" in params) if params else None,
            created=_date(it.get("created")),
        ))
    return rows


def _read_huggingface(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        inputs = _lst(it, "architecture.input_modalities")
        live = next((p for p in _lst(it, "providers") if isinstance(p, dict) and p.get("status") == "live"), None) or {}
        rows.append(_row(
            mid, mid, CHAT, "provider",  # the router serves chat-completion models by definition
            context=_first_int(live, "context_length"),
            input_image=("image" in inputs) if inputs else None,
            tools=_bool(live.get("supports_tools")),
            created=_date(it.get("created")),
        ))
    return rows


def _read_novita(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        inputs, outputs = _lst(it, "input_modalities"), _lst(it, "output_modalities")
        kind = _modality_kind(inputs, outputs)
        if kind == CHAT and _str(it.get("model_type")) not in (None, "chat"):
            kind = "specialised"
        features = [str(f) for f in _lst(it, "features")]
        rows.append(_row(
            mid, _label(it, mid, "display_name", "title"), kind, "provider" if kind else "none",
            detail=_str(it.get("model_type")) if kind == "specialised" else None,
            context=_first_int(it, "context_size"),
            max_output=_first_int(it, "max_output_tokens"),
            input_image=("image" in inputs) if inputs else None,
            tools=("function-calling" in features) if features else None,
            reasoning=("reasoning" in features) if features else None,
            created=_date(it.get("created")),
        ))
    return rows


def _read_atlascloud(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        inputs, outputs = _lst(it, "input_modalities"), _lst(it, "output_modalities")
        kind = _modality_kind(inputs, outputs)
        features = [str(f) for f in _lst(it, "supported_features")]
        created = _date(it.get("created"))
        rows.append(_row(
            mid, _label(it, mid, "name"), kind, "provider" if kind else "none",
            context=_first_int(it, "context_length"),
            max_output=_first_int(it, "max_output_length"),
            input_image=("image" in inputs) if inputs else None,
            tools=("tools" in features) if features else None,
            reasoning=("reasoning" in features) if features else None,
            created=None if created == "2021-07-20" else created,  # 1626777600 is a placeholder on many rows
        ))
    return rows


_TOGETHER_TYPES = {
    "chat": CHAT, "image": "image", "video": "video", "audio": "text_to_speech", "transcribe": "speech_to_text",
    "rerank": "rerank", "embedding": "embedding", "moderation": "moderation", "language": "completion", "code": "completion",
}


def _read_together(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        kind_word = _str(it.get("type"))
        kind = _TOGETHER_TYPES.get(kind_word.lower(), "specialised") if kind_word else None
        rows.append(_row(
            mid, _label(it, mid, "display_name"), kind, "provider" if kind else "none",
            detail=kind_word if kind == "specialised" else None,
            context=_first_int(it, "context_length"),
            created=_date(it.get("created")),
        ))
    return rows


_GROQ_RULES: list[Rule] = [("prompt-guard", "moderation", None)]


def _read_groq(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        inputs, outputs = _lst(it, "input_modalities"), _lst(it, "output_modalities")
        kind = _modality_kind(inputs, outputs)
        features = [str(f) for f in _lst(it, "supported_features")]
        row = _row(
            mid, _label(it, mid, "name"), kind, "provider" if kind else "none",
            context=_first_int(it, "context_window", "context_length"),
            max_output=_first_int(it, "max_completion_tokens", "max_output_length"),
            input_image=("image" in inputs) if inputs else None,
            tools=("tools" in features) if features else None,
            reasoning=("reasoning" in features) if features else None,
            created=_date(it.get("created")),
        )
        rows.append(_with_name_rule(row, _GROQ_RULES))  # the Prompt Guard classifiers answer text too
    return rows


def _read_fireworks(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        kind_word = _str(it.get("kind"))
        chat = _bool(it.get("supports_chat"))
        if kind_word == "EMBEDDING_MODEL":
            kind = "rerank" if "rerank" in mid.lower() else "embedding"
        elif kind_word == "ROUTER" or chat is None:
            kind = CHAT if kind_word else None
        else:
            kind = CHAT if chat else "completion"
        rows.append(_row(
            mid, mid, kind, "provider" if kind else "none",
            context=_first_int(it, "context_length"),
            input_image=_bool(it.get("supports_image_input")),
            tools=_bool(it.get("supports_tools")),
            created=_date(it.get("created")),
        ))
    return rows


def _read_mistral(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows: list[LiveModel] = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        caps = it.get("capabilities") if isinstance(it.get("capabilities"), dict) else {}
        if caps.get("completion_chat"):
            kind, detail = CHAT, None
        elif caps.get("audio_transcription") or caps.get("audio_transcription_realtime"):
            kind, detail = "speech_to_text", None
        elif caps.get("audio_speech"):
            kind, detail = "text_to_speech", None
        elif caps.get("ocr"):
            kind, detail = "document", None
        elif caps.get("moderation") or caps.get("classification"):
            kind, detail = "moderation", None
        elif caps.get("completion_fim"):
            kind, detail = "completion", None
        elif "embed" in mid.lower():
            kind, detail = "embedding", None
        else:
            kind, detail = ("specialised", _str(it.get("type")) or "other tasks") if caps else (None, None)
        retires, past = _retirement(it.get("deprecation"))
        if past:
            kind = "retired"
        aliases = tuple(str(a) for a in _lst(it, "aliases") if isinstance(a, str) and a != mid)
        rows.append(_row(
            mid, _label(it, mid, "name") if _str(it.get("name")) != mid else mid, kind, "provider" if caps else "none",
            detail=detail,
            context=_first_int(it, "max_context_length"),
            input_image=_bool(caps.get("vision")),
            tools=_bool(caps.get("function_calling")),
            reasoning=_bool(caps.get("reasoning")),
            retires_at=retires,
            created=_date(it.get("created")),
            aliases=aliases,
        ))
    # Every alias is its own row on Mistral (`mistral-large-latest` and `mistral-large-2512`
    # share one `name`): keep the row whose id is the name, fold the others into it.
    by_name: dict[str, list[LiveModel]] = {}
    for it, row in zip([i for i in items if _ident(i, "id")], rows):
        by_name.setdefault(_str(it.get("name")) or row.id, []).append(row)
    out: list[LiveModel] = []
    for name, group in by_name.items():
        keep = next((r for r in group if r.id == name), group[0])
        others = [r.id for r in group if r.id != keep.id]
        aliases = tuple(dict.fromkeys([*keep.aliases, *others]))
        out.append(LiveModel(**{**asdict(keep), "label": keep.id if keep.label == name else keep.label, "aliases": aliases}))
    return out


def _read_cohere(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "name", "id")
        if not mid:
            continue
        endpoints = [str(e) for e in _lst(it, "endpoints")]
        if not endpoints:
            kind = None
        elif "chat" in endpoints:
            kind = CHAT
        elif "embed" in endpoints:
            kind = "embedding"
        elif "rerank" in endpoints:
            kind = "rerank"
        elif "classify" in endpoints:
            kind = "moderation"
        else:
            kind = "specialised"
        features = [str(f) for f in _lst(it, "features")]
        rows.append(_row(
            mid, mid, kind, "provider" if kind else "none",
            detail=endpoints[0] if kind == "specialised" else None,
            context=_first_int(it, "context_length"),
            input_image=("vision" in features) if features else None,
            tools=("tools" in features) if features else None,
            reasoning=("reasoning" in features) if features else None,
        ))
    return rows


_ARK_DOMAINS = {"LLM": CHAT, "VLM": CHAT, "ImageGeneration": "image", "VideoGeneration": "video", "Embedding": "embedding", "3DGeneration": "specialised"}


def _read_ark(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        inputs, outputs = _lst(it, "modalities.input_modalities"), _lst(it, "modalities.output_modalities")
        domain = _str(it.get("domain"))
        kind = _modality_kind(inputs, outputs)
        if domain:
            by_domain = _ARK_DOMAINS.get(domain)
            if by_domain and by_domain != CHAT:
                kind = by_domain
            elif by_domain is None and domain == "Audio":
                kind = kind if kind in ("speech_to_text", "text_to_speech") else "text_to_speech"
            elif by_domain is None:
                kind = "specialised"
            elif kind is None:
                kind = CHAT
        rows.append(_row(
            mid, _label(it, mid, "name"), kind, "provider" if (kind or domain) else "none",
            detail=domain if kind == "specialised" else None,
            context=_first_int(it, "context_window", "token_limits.context_window"),
            max_output=_first_int(it, "max_output_tokens", "token_limits.max_output_token_length"),
            input_image=("image" in inputs) if inputs else None,
            tools=_bool(_get(it, "features.tools.function_calling")),
            created=_date(it.get("created")),
        ))
    return rows


def _read_deepseek(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        inputs, outputs = _lst(it, "input_modalities"), _lst(it, "output_modalities")
        kind = _modality_kind(inputs, outputs)
        rows.append(_row(
            mid, _label(it, mid, "name"), kind, "provider" if kind else "none",
            context=_first_int(it, "context_window"),
            max_output=_first_int(it, "max_output_tokens"),
            input_image=("image" in inputs) if inputs else None,
            reasoning=True if isinstance(it.get("effort"), dict) else None,
            created=_date(it.get("created")),
        ))
    return rows


def _read_kimi(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        inputs, outputs = _lst(it, "modalities.input"), _lst(it, "modalities.output")
        kind = _modality_kind(inputs, outputs)
        rows.append(_row(
            mid, mid, kind, "provider" if kind else "none",
            context=_first_int(it, "context_length"),
            input_image=_bool(it.get("supports_image_in")) if "supports_image_in" in it else (("image" in inputs) if inputs else None),
            tools=True if isinstance(it.get("tools"), dict) else None,
            reasoning=_bool(it.get("supports_reasoning")),
            created=_date(it.get("created")),
        ))
    return rows


def _read_anthropic(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        caps = it.get("capabilities") if isinstance(it.get("capabilities"), dict) else {}
        retires, past = _retirement(it.get("retires_at"))
        kind = "retired" if past or _str(it.get("lifecycle")) == "retired" else CHAT
        rows.append(_row(
            mid, _label(it, mid, "display_name"), kind, "provider",
            context=_first_int(it, "max_input_tokens"),
            max_output=_first_int(it, "max_tokens"),
            input_image=_bool(_get(caps, "image_input.supported")),
            input_pdf=_bool(_get(caps, "pdf_input.supported")),
            tools=True,
            reasoning=_bool(_get(caps, "thinking.supported")),
            retires_at=retires,
            created=_date(it.get("created_at")),
        ))
    return _fold(rows, lambda i: _ANTHROPIC_DATED.sub("", i) if _ANTHROPIC_DATED.search(i) else None)


def _read_xai(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    windows = {str(e.get("id")): e for e in extra if isinstance(e, dict) and e.get("id")}
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        inputs, outputs = _lst(it, "input_modalities"), _lst(it, "output_modalities")
        kind = _modality_kind(inputs, outputs)
        joined = windows.get(mid) or {}
        rows.append(_row(
            mid, mid, kind, "provider" if kind else "none",
            context=_first_int(joined, "context_length") or _first_int(it, "context_length"),
            input_image=("image" in inputs) if inputs else None,
            reasoning=True if isinstance(_get(it, "capabilities.reasoning_effort"), (dict, list)) or _get(it, "capabilities.reasoning_effort") is True else None,
            created=_date(it.get("created")),
            aliases=tuple(str(a) for a in _lst(it, "aliases") if isinstance(a, str) and a != mid),
        ))
    return rows


def _read_codex(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "slug", "id")
        if not mid:
            continue
        if _str(it.get("visibility")) == "hide":
            continue  # the backend hides it itself; it stays in all_ids, so a typed id is accepted
        inputs = _lst(it, "input_modalities")
        rows.append(_row(
            mid, _label(it, mid, "display_name"), CHAT, "provider",
            context=_first_int(it, "context_window"),
            input_image=("image" in inputs) if inputs else None,
            tools=True,
            reasoning=True if _lst(it, "supported_reasoning_levels") else None,
        ))
    return rows


_GEMINI_RULES: list[Rule] = [
    ("-image", "image", None), ("imagen", "image", None), ("banana", "image", None),
    ("-tts", "text_to_speech", None), ("lyria", "music", None), ("music", "music", None), ("veo", "video", None),
    ("robotics", "specialised", "robotics"), ("transcribe", "speech_to_text", None), ("translate", "specialised", "translation"),
    ("native-audio", "live", None), ("-live", "live", None), ("computer-use", "specialised", "computer use"),
    ("deep-research", "specialised", "deep research"), ("antigravity", "specialised", "coding agent"),
    ("aqa", "specialised", "attributed question answering"),
]


def _read_gemini(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        raw = _ident(it, "name", "id")
        if not raw:
            continue
        mid = raw[len("models/"):] if raw.startswith("models/") else raw
        methods = [str(m) for m in _lst(it, "supportedGenerationMethods")]
        if not methods or "generateContent" in methods:
            kind, source, detail = None, "none", None
        elif "bidiGenerateContent" in methods:
            kind, source, detail = "live", "provider", None
        elif any("embed" in m.lower() for m in methods):
            kind, source, detail = "embedding", "provider", None
        else:
            kind, source, detail = "specialised", "provider", methods[0]
        row = _row(
            mid, _label(it, raw, "displayName"), kind, source, detail=detail,
            context=_first_int(it, "inputTokenLimit"),
            max_output=_first_int(it, "outputTokenLimit"),
            reasoning=True if it.get("thinking") is True else None,
        )
        # Google serves image, music, video and robotics models through generateContent
        # too (seen live 2026-11-09): the method does not tell, the names do.
        rows.append(_with_name_rule(row, _GEMINI_RULES))
    return rows


_OPENAI_RULES: list[Rule] = [
    ("embedding", "embedding", None), ("tts", "text_to_speech", None), ("whisper", "speech_to_text", None),
    ("transcribe", "speech_to_text", None), ("dall-e", "image", None), ("image", "image", None), ("sora", "video", None),
    ("moderation", "moderation", None), ("realtime", "live", None), ("-live", "live", None), ("audio", "hybrid_audio", None),
    ("search-api", "specialised", "web search"),
]


def _read_openai(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        retires, past = _retirement(it.get("shutdown_date"))
        row = _row(mid, mid, "retired" if past else None, "provider" if past else "none", retires_at=retires, created=_date(it.get("created")))
        rows.append(_with_name_rule(row, _OPENAI_RULES))
    return _fold(rows, lambda i: _OPENAI_DATED.sub("", i) if _OPENAI_DATED.search(i) else None)


_NVIDIA_RULES: list[Rule] = [
    ("embed", "embedding", None), ("rerank", "rerank", None), ("nemoretriever", "embedding", None),
    ("riva", "specialised", "speech and translation"), ("detector", "specialised", "detection"),
]
_META_RULES: list[Rule] = [("muse-image", "image", None), ("muse-voice", "speech_to_text", None), ("sam-", "specialised", "segmentation")]
_MINIMAX_RULES: list[Rule] = [("speech", "text_to_speech", None), ("t2a", "text_to_speech", None), ("voice", "text_to_speech", None),
                              ("hailuo", "video", None), ("video", "video", None), ("music", "music", None)]


def _bare_reader(rules: list[Rule]) -> Callable[[list[dict[str, Any]], list[dict[str, Any]]], list[LiveModel]]:
    def read(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
        rows = []
        for it in items:
            mid = _ident(it, "id", "name")
            if not mid:
                continue
            row = _row(
                mid, _label(it, mid, "display_name", "name"), None, "none",
                context=_first_int(it, "context_length", "context_window", "max_context_length"),
                max_output=_first_int(it, "max_completion_tokens", "max_output_tokens"),
                created=_date(it.get("created")),
            )
            rows.append(_with_name_rule(row, rules))
        return rows

    return read


def _read_nebius(items: list[dict[str, Any]], extra: list[dict[str, Any]]) -> list[LiveModel]:
    rows = []
    for it in items:
        mid = _ident(it, "id")
        if not mid:
            continue
        modality = _str(_get(it, "architecture.modality"))  # "text+image->text"
        kind = None
        if modality and "->" in modality:
            inputs, outputs = modality.split("->", 1)
            kind = _modality_kind(inputs.split("+"), outputs.split("+"))
        rows.append(_row(
            mid, _label(it, mid, "name"), kind, "provider" if kind else "none",
            context=_first_int(it, "context_length"),
            input_image=("image" in modality.split("->", 1)[0]) if modality and "->" in modality else None,
            created=_date(it.get("created")),
        ))
    return rows


_READERS: dict[str, Callable[[list[dict[str, Any]], list[dict[str, Any]]], list[LiveModel]]] = {
    "openrouter": _read_openrouter,
    "openrouter-account": _read_openrouter,
    "orcarouter": _read_orcarouter,
    "huggingface": _read_huggingface,
    "novita": _read_novita,
    "atlascloud": _read_atlascloud,
    "together": _read_together,
    "groq": _read_groq,
    "fireworks": _read_fireworks,
    "mistral": _read_mistral,
    "cohere": _read_cohere,
    "ark": _read_ark,
    "deepseek": _read_deepseek,
    "kimi": _read_kimi,
    "anthropic": _read_anthropic,
    "xai": _read_xai,
    "openai-codex": _read_codex,
    "gemini": _read_gemini,
    "openai": _read_openai,
    "azure": _read_openai,
    "nvidia": _bare_reader(_NVIDIA_RULES),
    "meta": _bare_reader(_META_RULES),
    "minimax": _bare_reader(_MINIMAX_RULES),
    "nebius": _read_nebius,
}
_BARE = _bare_reader([])  # Z.AI, Cerebras, SambaNova, Upstage, Qwen compat, SiliconFlow (?sub_type=chat), custom servers


def _items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        items = payload.get("data")
        if not isinstance(items, list):
            items = payload.get("models")
    else:
        items = payload
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def parse_listing(name: str, payload: Any, extra: Any = None) -> list[LiveModel]:
    """Every model in a provider's answer, newest first, each with its kind. Tolerant of the
    shapes in use: {"data": [...]} (OpenAI and most), {"models": [...]} (Gemini, Cohere, the
    Codex backend), a bare list (Together)."""
    reader = _READERS.get(name, _BARE)
    rows: dict[str, LiveModel] = {}
    for row in reader(_items(payload), _items(extra)):
        if row.id not in rows:
            rows[row.id] = row
    # Two ids that read the same once their namespace is dropped (`auto`, `firerouter/auto`)
    # keep the namespace in the name, so the list never shows two identical names.
    counts: dict[str, int] = {}
    for row in rows.values():
        counts[row.label] = counts.get(row.label, 0) + 1
    for mid, row in list(rows.items()):
        if counts[row.label] > 1 and row.label == display_label(mid):
            fuller = display_label(_FIREWORKS_NAMESPACE.sub("", mid).replace("/", "-"))
            if fuller != row.label:
                rows[mid] = LiveModel(**{**asdict(row), "label": fuller})
    return sorted(rows.values(), key=lambda r: (r.created or "", r.id), reverse=True)


def listed_ids(name: str, payload: Any, rows: Optional[list[LiveModel]] = None) -> list[str]:
    """Every id in the answer, chat or not, hidden or not, plus every alias the rows carry,
    in the provider's order."""
    out: list[str] = []
    for item in _items(payload):
        raw_id = _ident(item, "id", "slug", "name")
        if not raw_id:
            continue
        model_id = raw_id[len("models/"):] if name == "gemini" and raw_id.startswith("models/") else raw_id
        if model_id not in out:
            out.append(model_id)
    for row in rows or []:
        for alias in (row.id, *row.aliases):
            if alias not in out:
                out.append(alias)
    return out


def fetch(name: str, profile: dict[str, Any], descriptor: Any, *, timeout: float = LISTING_TIMEOUT) -> Listing:
    """Ask the provider. Never raises: a provider that cannot be asked, reached or read
    comes back as a Listing with ok=False and a one-line reason."""
    if name in NO_LISTING:
        return Listing(name, False, [], error=NO_LISTING[name])
    request = listing_request(name, profile, descriptor)
    if request is None:
        return Listing(name, False, [], error="This provider has no model list to ask.")
    try:
        resp = _ask(request, timeout)
    except Exception as exc:  # noqa: BLE001 - a listing never raises into the route
        return Listing(name, False, [], error=f"Could not reach the provider: {exc.__class__.__name__}.")
    if resp.status_code in (401, 403):
        return Listing(name, False, [], error="The provider refused the key.", status=resp.status_code)
    if resp.status_code >= 300:
        return Listing(name, False, [], error=f"HTTP {resp.status_code} from the provider's model list.", status=resp.status_code)
    try:
        payload = resp.json()
    except ValueError:
        return Listing(name, False, [], error="The provider's model list was not JSON.", status=resp.status_code)
    extra_payload: Any = None
    if request.get("extra"):
        # The second call only adds facts (xAI's windows): losing it costs chips, not rows.
        try:
            second = _ask(request["extra"], timeout)
            extra_payload = second.json() if second.status_code < 300 else None
        except Exception:  # noqa: BLE001
            extra_payload = None
    rows = parse_listing(name, payload, extra_payload)
    return Listing(name, True, rows, status=resp.status_code, all_ids=listed_ids(name, payload, rows))


def _ask(request: dict[str, Any], timeout: float) -> Any:
    url = request["url"]
    if request.get("params"):
        from urllib.parse import urlencode

        url = url + "?" + urlencode(request["params"])
    return probe_http.get(url, headers=request.get("headers") or {}, timeout=timeout)


# -- facts the rest of the app can read without the manager --------------------------------
#
# The manager writes `live_facts.json` beside its prefs: {full model id: {label, context,
# tools, vision, pdf}} for the picker's models, as the provider's own list stated them. The
# engine's window resolution and `capabilities_for` read it through the helpers below.

_facts_lock = threading.Lock()
_facts_path: Optional[Path] = None
_facts_cache: tuple[float, dict[str, dict[str, Any]]] = (0.0, {})


def facts_path() -> Path:
    global _facts_path
    if _facts_path is None:
        from ..secrets import state_dir

        _facts_path = state_dir() / "live_facts.json"
    return _facts_path


def use_facts_path(path: Optional[Path]) -> None:
    """Tests and the manager: where the facts file lives (None → the state dir)."""
    global _facts_path, _facts_cache
    with _facts_lock:
        _facts_path = path
        _facts_cache = (0.0, {})


def write_facts(facts: dict[str, dict[str, Any]]) -> None:
    path = facts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(facts, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with _facts_lock:
        global _facts_cache
        _facts_cache = (0.0, {})


def all_facts() -> dict[str, dict[str, Any]]:
    """The facts file, re-read when it changed on disk."""
    global _facts_cache
    path = facts_path()
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return {}
    with _facts_lock:
        if _facts_cache[0] == stamp and _facts_cache[1]:
            return dict(_facts_cache[1])
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    facts = {k: v for k, v in doc.items() if isinstance(k, str) and isinstance(v, dict)} if isinstance(doc, dict) else {}
    with _facts_lock:
        _facts_cache = (stamp, facts)
    return dict(facts)


def facts_for(model: str) -> dict[str, Any]:
    return dict(all_facts().get(model) or {})


def context_window_for(model: str) -> Optional[int]:
    return _int(facts_for(model).get("context"))
