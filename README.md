<p align="center">
  <strong>LOG-mcp</strong><br>
  <em>Stop guessing which AI model to use. Let your own judgment build the answer.</em>
</p>

---

Every AI gateway routes your prompts. None of them learn from your choices.

LOG-mcp sends your prompt to multiple models simultaneously, you pick the best response, and the system builds a comparative dataset from your judgment. Over time it learns which models excel at *your* specific tasks — not synthetic benchmarks, not marketing claims, but your actual usage patterns.

It also strips your personal data before it reaches any cloud API, caches similar queries locally, and exports everything you need to fine-tune a local model that gradually replaces the cloud entirely.

**The draft round isn't a feature. It's a data collection primitive that doesn't exist anywhere else.**

## How It Works

```
Your prompt
    │
    ▼
┌─────────────────────────────────┐
│  🎯 precise    (temp 0.2)       │
│  💡 creative   (temp 0.7)       │──► You pick the winner
│  🧠 deep       (reasoner)       │
└─────────────────────────────────┘
    │
    ▼
Routing learns: "For this user, code questions → reasoner,
                 creative writing → creative, facts → precise"
    │
    ▼
Eventually: draft rankings become training data →
            fine-tuned local model replaces cloud API
```

## Why This Is Different

**Every other AI gateway** (LiteLLM, OpenRouter, Portkey, Helicone) solves one problem: call multiple providers through one API. They're middleware for routing. You pick models based on benchmark scores, pricing pages, or vibes.

**LOG-mcp solves a different problem:** building a dataset from your actual preferences that makes routing, caching, and eventually local inference provably better over time.

| | Other Gateways | LOG-mcp |
|--|---------------|---------|
| Route to multiple providers | ✅ | ✅ |
| Learn which provider you prefer | ❌ | ✅ (draft comparison) |
| Privacy: strip PII before cloud API | ❌ (rare) | ✅ (default) |
| Cache semantically similar queries | ❌ (rare) | ✅ (local embeddings) |
| Export preference data for training | ❌ | ✅ (LoRA/DPO format) |
| Run local models with GPU isolation | ❌ | ✅ (subprocess mode) |
| Self-hosted, single binary | Sometimes | ✅ (Python, SQLite, no runtime deps) |

The moat isn't the code. It's the comparative dataset — the same prompt, multiple models, human judgment, repeated thousands of times. That dataset doesn't exist publicly, and you can't buy it.

## Who Is This For

**Developers building AI-powered apps.** You're currently calling one model and hoping it's good enough. LOG-mcp gives you an OpenAI-compatible API that automatically picks the best model for each query, based on your users' actual feedback.

**Power users who talk to AI all day.** You're paying for multiple subscriptions and manually switching between ChatGPT, Claude, and DeepSeek depending on the task. LOG-mcp gives you one interface that routes intelligently and learns your preferences.

**Teams with privacy requirements.** You can't send customer emails, employee names, or financial data to OpenAI. LOG-mcp strips PII before it leaves your server and puts it back in the response. Your AI provider never sees personal data.

**People who want to own their AI stack.** Today you use cloud APIs. Tomorrow you want a local model that's as good. LOG-mcp's training pipeline turns your draft rankings into fine-tuning data for that transition.

## Quick Start

```bash
git clone https://github.com/CedarBeach2019/LOG-mcp.git
cd LOG-mcp
cp .env.example .env       # Edit with your API key and passphrase
pip install -r requirements.txt
python -m gateway.server
```

Open `http://localhost:8000`. That's it.

Works with **DeepSeek** out of the box (free tier available). Also supports Groq, OpenAI, OpenRouter, and local GGUF models.

### Docker

```bash
cp .env.example .env        # Edit first
docker compose up -d
```

### Using as an API

Drop-in replacement for any OpenAI SDK:

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:8000/v1", api_key="your-passphrase")
# That's not an API key — it's your LOG-mcp passphrase

response = client.chat.completions.create(
    model="auto",  # LOG-mcp picks the best model
    messages=[{"role": "user", "content": "Write a Python sort function"}],
)

# Route badge tells you which model was used
print(response.choices[0].message.content)
```

## What's Under the Hood

### Privacy Pipeline
Every request passes through dehydration before reaching a cloud API. Emails become `[EMAIL_1]`, phone numbers become `[PHONE_1]`, names become `[PERSON_1]`. The PII map is stored locally and used to rehydrate the response. The cloud API never sees your data.

### Intelligent Routing
A pattern-matching classifier categorizes every message (code, creative, factual, debug, etc.) and routes to the appropriate model. The classifier improves over time from your feedback — not by training a model, but by updating rules based on what actually worked.

### Draft Comparison
The headline feature. Toggle draft mode and your prompt goes to 3 profiles simultaneously (configurable: different models, temperatures, system prompts). You see all responses, pick the winner, and optionally elaborate. Every ranking is stored and feeds the training pipeline.

### Adaptive Learning
Tracks model reliability (does it crash?), response quality (do you thumbs-up?), latency, and estimated cost. Routes around degraded providers automatically. Over time, builds a profile of which model excels at which task *for you*.

### Semantic Cache
Locally-hosted embedding model (optional) caches semantically similar queries. "What is 2+2?" and "What does two plus two equal?" hit the same cache entry. Reduces API costs and latency.

### Training Pipeline
Exports your draft rankings as properly formatted LoRA and DPO training data. The dataset includes the prompt, the winning response (chosen), the losing response (rejected), and quality metadata. Feed this into any fine-tuning framework to create a model tuned to your preferences.

### Local Inference
Run GGUF models (Llama, Qwen, Phi, Mistral) directly on your hardware. On constrained devices (Jetson, Raspberry Pi), models run in an isolated subprocess to avoid GPU memory conflicts. Hot-swap models without downtime.

## Architecture

```
┌──────────────┐     ┌──────────────────────────────────────────┐
│   Client     │────►│              Gateway (Starlette)          │
│  Web / SDK   │     │                                          │
└──────────────┘     │  Auth → PII Strip → Route → Model Call   │
                     │  → PII Restore → Cache → Respond         │
                     │                                          │
                     │  ┌─────────┐ ┌──────────┐ ┌───────────┐ │
                     │  │ Router  │ │ Draft    │ │ Adaptive  │ │
                     │  │ Rules   │ │ Compare  │ │ Learner   │ │
                     │  └─────────┘ └──────────┘ └───────────┘ │
                     └──────────────────┬───────────────────────┘
                                        │
                     ┌──────────────────┼───────────────────────┐
                     │                  │                       │
                ┌────▼────┐      ┌─────▼──────┐      ┌────────▼────┐
                │ DeepSeek │      │    Groq    │      │   Local     │
                │  (API)   │      │   (API)    │      │  (GGUF)    │
                └──────────┘      └────────────┘      └─────────────┘
```

[Full architecture docs](docs/ARCHITECTURE.md)

## Configuration

```bash
# Required
LOG_API_KEY=sk-...                # DeepSeek API key (get one free at platform.deepseek.com)
LOG_PASSPHRASE=a-secret-phrase    # Login passphrase for the web UI and API

# Optional
LOG_CHEAP_MODEL=deepseek-chat     # Model for simple queries (default: deepseek-chat)
LOG_ESCALATION_MODEL=deepseek-reasoner  # Model for complex queries (default: deepseek-reasoner)
LOG_PRIVACY_MODE=true             # Strip PII before cloud API calls (default: true)
LOG_CACHE_ENABLED=true            # Cache similar queries locally (default: true)
LOG_DB_PATH=~/.log/vault.db       # Where to store your data (default: ~/.log/vault.db)
LOG_CORS_ORIGINS=http://localhost:8000  # Allowed origins (set to * to allow all)
LOG_JWT_SECRET=                   # JWT signing key (auto-generated if not set)
LOG_STREAM_TIMEOUT=120            # Max seconds for streaming responses (default: 120)
LOG_MAX_BODY_SIZE=1048576         # Max request body size in bytes (default: 1MB)
```

See [.env.example](.env.example) for a complete template.

## API Endpoints

OpenAI-compatible at `POST /v1/chat/completions`. Also includes:

- `POST /v1/drafts` — Multi-model draft comparison
- `POST /v1/feedback` — Submit preference (thumbs up/down)
- `GET/POST/DELETE /v1/sessions` — Conversation history
- `GET/POST/DELETE /v1/preferences` — User preferences
- `GET/POST/DELETE /v1/profiles` — Provider profiles
- `GET /v1/health` — Deep health check (DB, model, disk, memory)
- `GET /v1/metrics` — Request metrics (latency, error rate, cache hits)
- `GET /v1/adaptive/dashboard` — Model health and cost tracking
- `GET /v1/discovery/search` — Browse available models
- `GET /v1/training/export` — Export training data
- `GET/PUT /v1/config` — Runtime configuration

[Full API reference](docs/ARCHITECTURE.md#api-endpoints)

## What You Need

- Python 3.10+
- A DeepSeek API key ([free tier](https://platform.deepseek.com)) — or any OpenAI-compatible API
- ~100MB disk for the app, ~1GB+ if you use local models
- Optional: CUDA GPU for local inference, sentence-transformers for semantic cache

## What's Working Now

✅ Core pipeline (PII strip → route → model call → response)  
✅ Draft comparison with user ranking  
✅ Feedback loop and preference learning  
✅ Multi-provider routing (DeepSeek, Groq, OpenAI, OpenRouter, local)  
✅ Adaptive model health scoring and cost tracking  
✅ Semantic caching with local embeddings  
✅ Local GGUF model inference with GPU subprocess isolation  
✅ Training data export (LoRA + DPO format)  
✅ Dataset quality scoring and deduplication  
✅ Prompt template selection and context window management  
✅ Session management, streaming, observability, rate limiting  
✅ Docker deployment  

## What's Coming

🔜 Provider management UI  
🔜 LoRA training runner (consume exported data)  
🔜 Evaluation harness (benchmark your fine-tuned models)  
🔜 Bulk annotation UI (review and rank past interactions)  
🔜 Mobile-responsive web UI  
🔜 OpenAI function/tool calling passthrough  

[Full roadmap](docs/ROADMAP-v4.md)

## Security & Privacy

- **PII stripping is on by default.** Emails, phone numbers, names, addresses, dates, SSNs, credit card numbers are replaced with tokens before reaching any cloud API.
- **All data stored locally** in SQLite. Nothing is sent to LOG-mcp servers — there are none.
- **JWT authentication** with configurable secret.
- **Timing-safe** passphrase comparison.
- **CORS locked to localhost** by default. Explicitly configure origins for production.
- **No telemetry.** No phone home. No analytics. Your data is yours.
- **Rate limiting** prevents abuse (60 req/min, 10 burst).
- **Request body size limits** prevent memory exhaustion.

## Development

```bash
# Install deps
pip install -r requirements.txt

# Run tests
make test
# or
python -m pytest tests/ -q

# Run the server
make run
# or
python -m gateway.server
```

518 tests passing. CI runs on Python 3.10, 3.11, 3.12.

## License

MIT

---

<p align="center">
  <strong>The moat isn't the code.</strong> It's the comparative dataset —<br>
  the same prompt, multiple models, human judgment, repeated thousands of times.
</p>
