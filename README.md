<!-- Badges -->
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)
![MIT License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-325%20passing-brightgreen)
![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker)

# LOG-mcp

**Your personal AI gateway.** Route every prompt to the right model, strip PII before it leaves your machine, compare draft responses side-by-side, and learn what you prefer — all self-hosted, all yours.

```text
You ask a question. LOG-mcp sends it to three models simultaneously:

  🎯 precise (deepseek-chat, temp=0.2):
  "Photosynthesis converts light energy into glucose."

  💡 creative (deepseek-chat, temp=0.7):
  "Plants are solar kitchens — they bake sugar from sunlight."

  🧠 deep (deepseek-reasoner, temp=0.1):
  "Chlorophyll absorbs blue/red photons, exciting electrons that drive
   the Calvin cycle to fix CO₂ into C₆H₁₂O₆."

  Pick the one you like. 👍👎 teaches the router next time.
```

## What It Does

- **Privacy-first** — PII (emails, phones, names, addresses) is stripped before reaching any cloud API and rehydrated in the response. Your data never leaves clean.
- **Intelligent routing** — regex + ML-optimized rules classify every message: cheap for facts, escalation for code, comparison for tradeoffs, local when available.
- **Draft comparison** — the core primitive. Multiple profiles respond in parallel, you pick the best. This generates unique comparative training data that doesn't exist anywhere else.
- **Adaptive learning** — tracks model health, API costs, and confidence calibration. Routes around degraded providers automatically.
- **Local inference** — runs GGUF models on your hardware (Jetson, laptop, server). Subprocess isolation prevents GPU memory conflicts.
- **Error resilience** — retry + fallback chain with friendly messages. Never a raw 502 to the user.
- **Training pipeline** — exports draft rankings as LoRA/DPO datasets for fine-tuning local models.

## Quick Start

```bash
# Clone and setup
git clone https://github.com/CedarBeach2019/LOG-mcp.git
cd LOG-mcp
pip install -r requirements.txt

# Set your API keys
export LOG_API_KEY="sk-your-deepseek-key"
export LOG_PASSPHRASE="your-secret-passphrase"

# Run
python -m gateway.server
```

Open `http://localhost:8000` and enter your passphrase.

## Features

| Feature | Description |
|---------|-------------|
| **PII Dehydration** | Strips emails, phones, names, addresses, dates before API calls |
| **Dynamic Routing** | Auto-optimizing classifier from user feedback |
| **Draft Round** | 3 profiles respond, you rank them |
| **Adaptive Router** | Model health scoring, cost tracking, confidence calibration |
| **Semantic Cache** | Cosine similarity cache with local embeddings |
| **Local Inference** | llama-cpp-python with subprocess GPU isolation |
| **Error Boundaries** | Retry + fallback + friendly error messages |
| **Session Management** | Persistent conversations with history |
| **Streaming** | Server-sent events with blinking cursor |
| **Observability** | Request tracing, latency metrics, per-request timing |
| **Rate Limiting** | Token bucket per IP (60/min, 10 burst) |
| **Training Export** | LoRA JSONL + DPO pairs from draft rankings |
| **Model Catalog** | Download GGUF models from HuggingFace |
| **Runtime Config** | Update settings without restart |
| **Prompt Templates** | 7 system prompts + context window management |

## Configuration

All settings via environment variables:

```bash
LOG_API_KEY=sk-...              # DeepSeek API key
LOG_PASSPHRASE=secret           # Login passphrase
LOG_CHEAP_MODEL=deepseek-chat   # Cheap model name
LOG_ESCALATION_MODEL=deepseek-reasoner  # Quality model name
LOG_DB_PATH=~/.log/vault.db     # SQLite database path
LOG_PRIVACY_MODE=true           # Enable PII stripping
LOG_CACHE_ENABLED=true          # Enable semantic cache
LOG_LOCAL_USE_SUBPROCESS=false  # Subprocess GPU isolation (auto on Jetson)
LOG_CORS_ORIGINS=*              # Comma-separated allowed origins
```

## Deployment

### Docker
```bash
docker compose up -d
```

### Bare Metal (Jetson)
```bash
# Subprocess mode auto-detected on Jetson (/etc/nv_tegra_release)
pip install -r requirements-jetson.txt
python -m gateway.server
```

### Cloudflare (PII only)
The Cloudflare Worker handles PII dehydration at the edge. See `cloudflare/` for setup.

## API

OpenAI-compatible `POST /v1/chat/completions`. Works with any OpenAI SDK or compatible client.

Full endpoint list in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Roadmap

See [docs/ROADMAP-v4.md](docs/ROADMAP-v4.md) for the full roadmap (Phase 4-7).

## License

MIT
