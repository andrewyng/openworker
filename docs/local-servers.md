# llama.cpp, vLLM, LM Studio, Jan, Docker Model Runner and mlx-lm

OpenWorker can use a model served by [llama.cpp](https://github.com/ggml-org/llama.cpp),
[vLLM](https://github.com/vllm-project/vllm), [LM Studio](https://lmstudio.ai),
[Jan](https://jan.ai), [Docker Model Runner](https://docs.docker.com/ai/model-runner/) or
[mlx-lm](https://github.com/ml-explore/mlx-lm), on this computer or on another machine.
Each is its own provider in Settings ▸ Models & Keys, under "On your own hardware", next
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

## Jan

Download a model in the Jan app, then go to Settings ▸ Local API Server, pick the model
and click Start Server. Jan listens on `127.0.0.1:1337` and serves the OpenAI API under
`/v1`.

- A key is needed only if you set one in the server's configuration.

The default address is `http://127.0.0.1:1337`.

## Docker Model Runner

Turn on Model Runner in Docker Desktop (Settings ▸ AI), with TCP host access, then pull a
model:

```bash
docker model pull hf.co/<repo>:<quant>
```

- The OpenAI API is served under `/engines/v1` on port 12434; typing
  `http://localhost:12434` or `http://localhost:12434/engines` both work.
- The model's id is the name you pulled (`hf.co/...` or `ai/...`).

The default address is `http://localhost:12434/engines`.

## mlx-lm

mlx-lm runs on Apple silicon:

```bash
pip install mlx-lm
mlx_lm.server --model mlx-community/<repo>
```

- It listens on `127.0.0.1:8080`, the same port as llama.cpp. If you run both, start one
  of them on another port, for example `mlx_lm.server --port 8081`, and type that address.
- The model's id is the repo you started it with.

The default address is `http://127.0.0.1:8080`.

## In the CLI

The model id is `<provider>:<id>` — `llamacpp:<id>`, `vllm:<id>`, `lmstudio:<id>`,
`jan:<id>`, `docker:<id>` or `mlx:<id>`:

```bash
openworker run --model llamacpp:my-model --prompt "..."
```

## Per-model settings

Longest reply, sampling, thinking and auto-compaction are set per model as for any other
provider; see [model settings](model-settings.md). The context size of these servers is
set when the server starts (or, in LM Studio, when it loads the model), so OpenWorker
shows it rather than setting it.
