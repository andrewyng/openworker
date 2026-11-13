# Run local models

Connect OpenWorker to a model server on your own hardware.

A local model server runs inference; OpenWorker supplies the agent loop and tools. For file tasks, coding or connected-app work, choose a model and server configuration that support **tool calling**. Text generation alone is not enough.

## Start with Ollama

Install and start [Ollama](https://ollama.com), then download a model suitable for your hardware and task. In **Settings → Models & Keys**, open **Ollama**, connect to the server and select an available model.

If the form offers model discovery or download, follow the controls shown there. Large downloads and larger context windows use disk space and memory; start with a model your machine can comfortably run.

Select that model in the composer and try a small task, such as reading one Markdown file and summarizing it. If it only describes a tool call instead of executing one, check the model's tool support and server settings.

## Use another local server

OpenWorker also supports several compatible servers. Choose the matching provider card when it is available in your build, or configure a compatible custom provider.

| Server | Common default address | What to check |
|---|---|---|
| llama.cpp | `http://localhost:8080` | Start `llama-server` with a suitable model and tool-capable chat template. |
| vLLM | `http://localhost:8000` | Enable automatic tool choice and the tool parser required by the model. |
| LM Studio | `http://localhost:1234` | Start its local server and load or enable the model. |
| Jan | `http://127.0.0.1:1337` | Start the Local API Server. |
| Docker Model Runner | `http://localhost:12434/engines` | Enable TCP host access and pull a model. |
| mlx-lm | `http://127.0.0.1:8080` | Start its server on Apple Silicon. Use another port if llama.cpp already uses this one. |

These are common defaults, not required ports. Enter the address your server actually uses. Supply an API key only when the server requires one.

## Balance context, memory and speed

A larger context window lets the model read more at once, but uses more memory. A longer reply limit lets it produce more output; it does not make the model faster or more capable.

Context size for servers such as llama.cpp and vLLM is set when the server starts. OpenWorker can show that value and use it for compaction, but changing an app preference cannot resize a running server's context window. LM Studio's loaded-model configuration has the same practical importance.

## A server on another computer

You can point a provider at a reachable model server on your network. That is different from [running the agent on a remote machine](machines.md): only model inference moves in this arrangement; the conversation and tools still run on the machine hosting the agent.

`localhost` always refers to the machine making the request. A remote agent pointed at `localhost:1234` looks for a server on the remote machine, not on your laptop. Use an address reachable from that machine and configure authentication and network access appropriately.

## Understand the privacy boundary

Local inference can keep model-bound context on your hardware. Tools may still access network services, and connected apps still send their requests. Review [privacy and stored data](privacy.md) before treating a workflow as fully offline.
