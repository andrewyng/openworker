# Model settings

Each model can carry its own settings: how much context it is loaded with, the longest
reply, whether it thinks, its sampling, and when the conversation is compacted. The
desktop app sets them in the model's settings dialog; the CLI reads the same file.

## Where they live

`model_config.json` in OpenWorker's state folder, keyed by the model id:

```json
{
  "ollama:nemotron-3.5-lightning:30b": {
    "context_size": 65536,
    "max_output_tokens": 16000,
    "thinking": true,
    "temperature": 1.0,
    "top_p": 0.95,
    "compaction_threshold_pct": 0.8,
    "default": true
  }
}
```

| Setting | What it does |
|---|---|
| `context_size` | The window a local model is loaded with. For Ollama it is sent as `num_ctx` on every request. It also sizes auto-compaction. For a cloud model the matrix does not list, it only sizes compaction. |
| `max_output_tokens` | The longest reply, sent as `max_tokens`. |
| `thinking` | On or off, for models with a thinking switch (Ollama's `think`). |
| `reasoning_effort` | The level for models with effort levels (`low`, `medium`, `high`). |
| `temperature`, `top_p` | Sampling. |
| `compaction_threshold_pct` | When to compact, as a share of the window (0.10 to 0.95). Overrides the machine-wide setting for this model. |
| `default` | True on the model new sessions start with. Only one model has it. |

A setting that is not saved falls back to the model's recommendation, then to the
provider's own default.

## Recommended settings

`coworker/providers/recommended_models.json` holds recommended settings per model, taken
from the model makers' own cards and guides, with a source link on every entry. A model is
matched on its name without the provider prefix, so an Ollama tag and a vLLM model name
find the same entry. Where a card gives separate values for reasoning and tool calling,
the table carries the tool-calling ones.

To add or change an entry, edit the file in git. Each entry has:

- `match`: name patterns (`*` wildcards), checked in order.
- `context_max`: the window the model was trained for.
- `context_for_agents`: the window the maker suggests for agent work.
- `max_output_tokens`, `thinking` (`available`, `default`), `sampling`.
- `source`: where the values come from.

## Context size on a local model

Without a saved `context_size`, OpenWorker chooses the window from the memory models load
into on this machine (an NVIDIA card's memory, else system memory): 16K under 16 GB, 32K
under 32 GB, 64K under 64 GB, 128K above, never more than the model's own limit. A
`num_ctx` pinned in an Ollama Modelfile is kept. `OPENWORKER_OLLAMA_NUM_CTX` caps any of
these.

## In a run

`openworker run` reads the saved settings for its model unless `--isolated` is given. A
flag on the run (`--max-output-tokens`, `--reasoning-effort`) wins over the saved value.

## The API

- `GET /v1/settings/model-config?model=<id>`: the settings in force, each with where it
  came from (`user` or `recommended`), plus the recommendation.
- `POST /v1/settings/model-config` with `{"model": "<id>", "values": {...}}`: save. A
  value of `null` removes that setting.
- `POST /v1/settings/model-config/remove` with `{"model": "<id>"}`.
