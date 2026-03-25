<!-- Badges -->
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)
![MIT License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-108%20passing-brightgreen)
![Docker](https://img.shields.io/badge/docker-ready-2496ED?logo=docker)
[![Cloudflare Workers](https://img.shields.io/badge/live-Cloudflare%20Workers-F38020?logo=cloudflare)](https://log-mcp-vault.magnus-digennaro.workers.dev/)

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

---

## Architecture

```
 ┌──────────────┐     ┌───────────────────────────────────┐     ┌──────────────┐
 │              │     │  LOG-mcp Gateway                   │     │              │
 │   Your App   │────▶│  ┌─────────┐  ┌───────────────┐  │────▶│  Cloud APIs  │
 │   / Agent    │     │  │ PII      │  │ Rule Router   │  │     │  DeepSeek    │
 │              │     │  │ Engine   │──│ (~5ms regex)  │  │     │  Claude      │
 │              │◀────│  └─────────┘  └──────┬────────┘  │◀────│  GPT         │
 └──────────────┘     │                      │            │     │  Ollama      │
                      │  ┌───────────────────▼────────┐  │     └──────────────┘
                      │  │   Draft Round               │  │
                      │  │   precise │ creative │ deep  │  │
                      │  │   → fire all → pick best    │  │
                      │  └────────────────────────────┘  │
                      │  ┌────────────────────────────┐  │
                      │  │   Local Vault (SQLite)      │  │
                      │  │   preferences · feedback    │  │
                      │  │   interactions · entities   │  │
                      │  └────────────────────────────┘  │
                      └───────────────────────────────────┘
```

**Message flow:** PII stripped → router classifies → draft round fires → response rehydrated. The AI never sees your data. You see the best answer.

---

## What It Does

### Intelligent Routing
Every message is classified in ~5ms using pattern matching. Simple questions ("what is 5km in miles?") go to the fast/cheap model. Complex ones ("debug my traceback" or "write an essay about…") escalate to a reasoning model. You can override with `/local`, `/cloud`, `/reason`, or `/draft`.

### Draft Round
For any request, fire multiple model profiles in parallel — each with different temperatures and system prompts. Pick the response that fits, or let the router learn from your choice over time.

### Privacy by Default
PII (names, emails, phones, SSNs, credit cards, API keys, addresses) is detected and replaced with tokens *before* any cloud API call. The mapping lives only in your local SQLite vault. Cloud providers never see your real data.

### Preference Learning
Every interaction is stored. Your 👍/👎 feedback and text critiques feed back into routing decisions. The system gets better at choosing the right model for *you* specifically — not some averaged user.

### OpenAI-Compatible API
Drop it in as a replacement for any OpenAI SDK. Same `/v1/chat/completions` endpoint, same response format. Works with Claude, GPT, DeepSeek, Ollama — whatever you configure.

---

## Quick Start

**Docker (recommended):**
```bash
git clone https://github.com/CedarBeach2019/LOG-mcp.git
cd LOG-mcp
docker compose up
```

That's it. The gateway is running on `http://localhost:8000`.

**Manual install:**
```bash
pip install -e ".[full]"
log init
```

**Point your app at it:**
```bash
export OPENAI_BASE_URL=http://localhost:8000/v1
export OPENAI_API_KEY=your-deepseek-key
```

---

## Deployment

| Mode | Setup | Best For |
|---|---|---|
| **Docker** | `docker compose up` | Self-hosted, air-gapped, one command |
| **Cloudflare Workers** | `cd cloudflare && npx wrangler deploy` | Global edge, free tier (100k req/day) |
| **Local** | `pip install -e .` | Development, scripting |

See [QUICKSTART.md](QUICKSTART.md) for the full walkthrough.

---

## CLI

```bash
log dehydrate "Call Jane Doe at jane@example.com"   # Strip PII
log scout deepseek "What is photosynthesis?"         # Route + respond
log draft "Explain quantum computing"                 # Draft round: 3 models
log stats                                            # Vault stats
log rehydrate <session-id>                           # Restore original text
```

---

## MCP Server

Use as a [Model Context Protocol](https://modelcontextprotocol.io/) server:

```json
{
  "mcpServers": {
    "log-vault": {
      "command": "python",
      "args": ["-m", "mcp.server"],
      "cwd": "/path/to/LOG-mcp"
    }
  }
}
```

Tools: `dehydrate`, `rehydrate`, `stats`, `list_sessions`, `draft`, `feedback`.

---

## For Developers

### Adding a Provider

Edit `vault/config.py` — add a new `cheap_model_endpoint` / `escalation_model_endpoint` pointing to any OpenAI-compatible API. That's it. The draft round and router will use it.

### Custom Routing

Edit `vault/routing_script.py`. The `RULES` dict maps regex patterns to routing actions (`CHEAP_ONLY`, `ESCALATE`). Add patterns for your domain:

```python
"ESCALATE": {
    "patterns": [
        r"my (legal|medical|financial)\b",  # your custom rule
        # ...
    ]
}
```

### Draft Profiles

Edit `vault/draft_profiles.py` to change the parallel draft personas (temperature, system prompt, model, max length).

### Configuration

All settings via `LOG_` environment variables. See `vault/config.py` for the full list. Key ones:

| Variable | Default | Purpose |
|---|---|---|
| `LOG_privacy_mode` | `true` | PII-strip before any cloud call |
| `LOG_instant_send` | `true` | Fire cheap model immediately |
| `LOG_draft_mode` | `true` | Enable draft round |
| `LOG_cheap_model_name` | `deepseek-chat` | Fast model |
| `LOG_escalation_model_name` | `deepseek-reasoner` | Reasoning model |
| `LOG_ollama_base_url` | `http://localhost:11434` | Local LLM |

---

## Testing

```bash
pytest tests/ -v                    # 108 tests
pytest --cov=vault --cov=gateway    # With coverage
```

---

## Project Links

| | |
|---|---|
| 🚀 [Quickstart Guide](QUICKSTART.md) | Up and running in 5 minutes |
| 🗺️ [Roadmap](ROADMAP.md) | What's coming next |
| 🤝 [Contributing](CONTRIBUTING.md) | How to contribute |
| 📐 [Architecture](docs/ARCHITECTURE.md) | Technical deep dive |
| 🔮 [Vision](docs/VISION.md) | Where this is heading |
| 📄 [License](LICENSE) | MIT |

---

## License

MIT — fork it, modify it, run it however you want. Your gateway, your rules.
