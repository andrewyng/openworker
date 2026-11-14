# Connect a model in the Desktop App

In the OpenWorker Desktop App, model access is yours: pick a provider, paste your key, switch anytime. Supported out of the box:

**OpenAI · Azure OpenAI · Anthropic · Google Gemini · BytePlus Ark · Volcengine Ark Agent Plan · Atlas Cloud · Inkling (Thinking Machines) · GLM (Z.ai) · DeepSeek · Kimi (Moonshot) · Qwen · MiniMax · Mistral · Grok (xAI) · Solar (Upstage) · Cohere** - plus aggregators **OrcaRouter**, **OpenRouter** and **Hugging Face Inference Providers**, multi-model routing through **[TrustedRouter](https://trustedrouter.com)**, open-weight models via **Together**, **Fireworks**, **Cerebras** and **SiliconFlow**, and fully local models via **Ollama**, llama.cpp, vLLM, LM Studio, Jan, Docker Model Runner or mlx-lm.

A curated model list marks what we've verified for tool-calling work. Adding any model string works at your own risk.

Azure OpenAI uses your own resource: paste its endpoint and key (or set `AZURE_OPENAI_API_KEY`), then add your deployment names as models — on Azure the model id is the deployment name.

Another OpenAI-compatible server — a gateway, a proxy, a vendor not listed? In **Settings ▸ Models & Keys**, the **Add an OpenAI-compatible provider** box at the end of the API-keys group takes a name, its endpoint and key; it becomes its own card, and its models are selected as `<prefix>:<model-id>` (the prefix is derived from the name). Type the model ids, or fetch them from the server when it lists them.

OpenRouter comes two ways under **Settings → Models & Keys**: **OpenRouter** (API
keys) takes a key you paste; **OpenRouter account** (Subscriptions) signs you in
through the browser and uses your OpenRouter credits — the key OpenRouter issues
stays on this computer. On a remote backend choose **Use a manual code** and paste
the code OpenRouter shows. Each card is its own provider with its own models, so
disconnecting the account never falls back to a pasted key. To revoke the issued
key on OpenRouter's side, use the **Manage or revoke keys on OpenRouter** link.

## Next steps

- [Local model servers](local-servers.md)
- [Per-model settings](model-settings.md)
- [Back to the Desktop App walkthrough](../README.md#try-your-first-task)
