# Machine inventory

Generated 2026-09-02T18:31:26Z by ci-sweep-stage. Every line below was read from this
machine at that moment. Do not carry version numbers in from anywhere else.

## Hardware and kernel
```
arch            aarch64
kernel          6.17.0-1029-nvidia
gpu             NVIDIA GB10
nvidia driver   580.173.02
cuda (nvcc)     13.0
rocm            absent -- this is not an AMD box
```

## Listening services
```
5002  UP    decode_proxy
5100  UP    vLLM
6333  UP    qdrant
3060  UP    langfuse
8765  UP    openworker
3000  UP    librechat
```

## Python packages, per environment that actually exists
```
~/prime-rl/.venv                                     vllm==0.26.0+cu129  torch==2.11.0+cu128  transformers==5.6.2  pydantic==2.13.4  cryptography==48.0.0  fastapi==0.136.3
~/openworker/.venv                                   pydantic==2.13.4  cryptography==50.0.0  fastapi==0.141.1
~/jabCreative/dataScience/agpack/.venv               pydantic==2.13.5  cryptography==50.0.1
~/jabCreative/quantumGlobalGroup/ragtradesystem/.venv torch==2.13.0  transformers==5.14.1  pydantic==2.13.4  cryptography==50.0.0
system python3                                       transformers==5.14.1  pydantic==2.13.4
```

## llama.cpp clones (HEAD, and whether the last commit is yours or upstream)
```
llama.cpp                          221f0f6  2026-08-02  (Talha Adnan)
llama.cpp-gb10                     0666ad2  2026-08-10  (Mario Limonciello)
llama.cpp-master                   38406d5  2026-08-11  (Bartowski)
llama.cpp-qwen4exp                 035e227  2026-08-26  (Daniel Han)
```

## Container images actually running
```
mcp-gateway  hwdsl2/mcp-gateway
dcode-vllm  vllm/vllm-openai:latest
admin-panel  registry.librechat.ai/clickhouse/librechat-admin-panel:1.0.0
rag_api  registry.librechat.ai/danny-avila/librechat-rag-api-dev-lite:v0.9.0
LibreChat  registry.librechat.ai/danny-avila/librechat:v0.8.7
chat-meilisearch  getmeili/meilisearch:v1.35.1
chat-mongodb  mongo:8.0.20
langfuse-langfuse-web-1  langfuse/langfuse:4
langfuse-langfuse-worker-1  langfuse/langfuse-worker:4
langfuse-minio-1  cgr.dev/chainguard/minio
langfuse-clickhouse-1  clickhouse/clickhouse-server:25.12
langfuse-redis-1  redis:7
langfuse-postgres-1  postgres:17
codeapi-sandbox-runner  code-interpreter-sandbox-runner
vectordb  pgvector/pgvector:0.8.0-pg15-trixie
qdrant  qdrant/qdrant
redis  redis:alpine
openshell-cluster-nemoclaw  ghcr.io/nvidia/openshell/cluster:0.0.36
```
