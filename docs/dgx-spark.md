# Running OpenWorker on NVIDIA DGX Spark

DGX Spark is a Linux ARM64 system. OpenWorker's agent and UI run on the CPU; a local model
server such as Ollama or vLLM uses the Blackwell GPU and exposes an API to OpenWorker.

## Prerequisites

On DGX OS (Ubuntu 24.04), install the browser-build requirements:

```shell
sudo apt update
sudo apt install -y build-essential curl git python3 python3-dev python3-venv
```

Install Node.js 20 or newer, then confirm `node --version` and `npm --version` work. For a
native desktop `.deb`, also install Rust with rustup and Tauri's Linux libraries:

```shell
sudo apt install -y \
  libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev \
  libssl-dev libgtk-3-dev libasound2-dev pkg-config
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

## Set up and run

From the repository root:

```shell
bash packaging/setup_dgx_spark.sh
bash packaging/run_dgx_spark.sh /path/to/your/project
```

Open `http://127.0.0.1:1420` on the Spark. If the Spark is remote, keep both services bound to
loopback and forward them over SSH:

```shell
ssh -L 1420:127.0.0.1:1420 -L 8765:127.0.0.1:8765 USER@SPARK_HOST
```

Then open `http://127.0.0.1:1420` on your laptop. The loopback-only design is intentional:
OpenWorker can run shell commands and access files, so its control API is not exposed directly
to the LAN.

## Use the Spark GPU with Ollama

NVIDIA documents Ollama as a supported local inference path on DGX Spark. Install it, pull an
agent-capable model, and check that its OpenAI-compatible endpoint is live:

```shell
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3.6:35b-a3b-nvfp4
curl http://127.0.0.1:11434/v1/models
```

In OpenWorker, open **Settings → Model providers → Ollama**, use
`http://127.0.0.1:11434`, then add the exact model tag
`ollama:qwen3.6:35b-a3b-nvfp4`. Model availability changes over time; use a smaller
tool-capable model if that tag is unavailable or if another workload is consuming unified
memory.

An existing vLLM server also works through OpenWorker's OpenAI provider: enter the server's
OpenAI-compatible `/v1` URL as the custom base URL, use any non-empty placeholder API key if
the server has authentication disabled, and add the exact served model ID.

## Build and install the desktop app

After setup and the native build prerequisites:

```shell
bash packaging/build_linux.sh
sudo apt install ./surfaces/gui/src-tauri/target/release/bundle/deb/*.deb
```

This package is built natively for the machine running the script, so a DGX Spark produces an
ARM64 package. Voice Input remains unavailable on Linux; chat, files, shell tools, connectors,
automations, and local model access are available.

