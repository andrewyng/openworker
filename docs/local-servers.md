# llama.cpp, vLLM and LM Studio

OpenWorker can use a model served by [llama.cpp](https://github.com/ggml-org/llama.cpp),
[vLLM](https://github.com/vllm-project/vllm) or [LM Studio](https://lmstudio.ai), on this
computer or on another machine. Each is its own provider in Settings ▸ Models & Keys, under "On your own hardware", next
to Ollama. Give it the server's address and, only if the server was started with one, its
API key. OpenWorker reads the rest from the server.

## llama.cpp

Start `llama-server` with a model file. It serves one model:

```bash
llama-server -m model.gguf --alias my-model -c 65536 -ngl 99 --port 8080
```

- `--alias` names the model; without it the model's id is the file's path.
- `-c` sets the context size. OpenWorker cannot change it while the server runs; the
  settings page shows it and sizes auto-compaction to it.
- Tool calling goes through the model's chat template (`--jinja`, on by default). Models
  whose template has no tool support cannot do agent work.
- A model file that Ollama downloaded can be served directly; the files under
  `~/.ollama/models/blobs` are GGUF.

The default address is `http://localhost:8080`.

## vLLM

vLLM runs on Linux with an NVIDIA GPU, so it is usually on another machine:

```bash
vllm serve nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4 \
    --enable-auto-tool-choice --tool-call-parser qwen3_coder --reasoning-parser nemotron_v3 \
    --max-model-len 65536 --host 0.0.0.0 --port 8000
```

- `--enable-auto-tool-choice --tool-call-parser <name>` turns tool calling on. Without
  them coworkers cannot work with the server; OpenWorker checks when it connects and says
  so, with the options to add. The parser name depends on the model; the model's card on
  Hugging Face gives it (the line above is NVIDIA's for Nemotron 3.5 Lightning).
- `--max-model-len` sets the context size, shown in the settings page and used for
  auto-compaction.
- The model's id is its Hugging Face name.

The default address is `http://localhost:8000`.

## LM Studio

Download a model in the LM Studio app, then start its server from the Developer tab or
with `lms server start`. Every downloaded model is listed; LM Studio loads one when the
first request names it.

- The model's id is its LM Studio catalog name, for example `qwen/qwen3-coder-30b`.
- The context size is the one LM Studio loaded the model with (its own listing reports
  it); until the model is loaded, the trained window is shown instead.
- A key is needed only if you turned one on in the app.

The default address is `http://localhost:1234`.

## In the CLI

The model id is `llamacpp:<id>`, `vllm:<id>` or `lmstudio:<id>`:

```bash
openworker run --model llamacpp:my-model --prompt "..."
```

## Per-model settings

Longest reply, sampling, thinking and auto-compaction are set per model as for any other
provider; see [model settings](model-settings.md). The context size of these servers is
set when the server starts (or, in LM Studio, when it loads the model), so OpenWorker
shows it rather than setting it.
