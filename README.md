<!-- Badges -->
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)
![MIT License](https://img.shields.io/badge/license-MIT-green)
![Tests](https://img.shields.io/badge/tests-52%20passing-brightgreen)
[![Deployed](https://img.shields.io/badge/live-Cloudflare%20Workers-orange)](https://log-mcp-vault.magnus-digennaro.workers.dev/)

# 🔒 LOG-mcp — Your PII never touches an AI server.

Privacy middleware that strips every trace of personal data from your messages *before* they reach any AI API. No trust required.

```
$ log dehydrate "Patient John Smith (DOB 1985-03-12) called from 555-123-4567"

  Dehydrated: "Patient ENTITY_1 (DOB [DOB]) called from PHONE_1"
  Rehydrate key: session_abc123

  → Send "Patient ENTITY_1 (DOB [DOB]) called from PHONE_1" to any AI.
  → The AI never sees John Smith, his birthday, or his phone number.
```

👉 **[Try it live](https://log-mcp-vault.magnus-digennaro.workers.dev/)** — hit the deployed Cloudflare Worker right now.

---

## Why this matters

| Scenario | Risk without LOG-mcp |
|---|---|
| **Healthcare** — Sending patient notes to an LLM for summarization | HIPAA violation. Real names, SSNs, and diagnoses leak to OpenAI/Anthropic servers. |
| **Legal** — Running attorney-client memos through AI for research | Attorney-client privilege destroyed. Case details stored in third-party training data. |
| **Finance** — Automating fraud analysis on transaction logs | PCI-DSS breach. Credit card numbers and account holders exposed to AI providers. |
| **Multi-agent** — Agents passing user context to sub-agents | Each hop is a potential PII leak. Every endpoint is an attack surface. |

LOG-mcp catches all of it *at the gateway*, before data leaves your infrastructure.

---

## Architecture

```
 ┌──────────┐      ┌──────────────┐      ┌───────────┐
 │  Your     │      │  LOG-mcp     │      │  AI API   │
 │  App /    │─────▶│  Gateway     │─────▶│           │
 │  Agent    │      │              │      │  Claude   │
 └──────────┘      │  dehydrate() │      │  GPT      │
                    │  → strip PII │      │  Gemini   │
 ┌──────────┐      │  → store map │      │  Llama    │
 │  Local    │◀─────│  rehydrate() │◀─────│           │
 │  Vault    │      │  → restore   │      └───────────┘
 │  (SQLite) │      │              │
 └──────────┘      └──────────────┘
```

Your data flows: **App → Gateway → AI** (anonymized). **AI → Gateway → App** (rehydrated). The AI only ever sees tokens like `ENTITY_1` and `PHONE_3`.

---

## Quick Install

```bash
git clone https://github.com/CedarBeach2019/LOG-mcp.git
cd LOG-mcp
pip install -e .
```

That's it. You're ready.

```bash
$ log dehydrate "Call Jane Doe at jane@example.com or 212-555-0147"
Dehydrated: "Call ENTITY_1 at EMAIL_1 or PHONE_1"
Session: sess_7f3a2c
```

---

## Deployment Modes

| Mode | Best for | Cost | Latency |
|---|---|---|---|
| **[Local](#local)** | Development, privacy-critical workloads | Free | Lowest |
| **[Cloudflare Workers](#cloudflare-workers)** | Production, serverless, global edge | Free tier | ~50ms |
| **[Docker](#docker)** | Self-hosted, air-gapped, on-prem | Infrastructure only | Network-dependent |

### Local

```bash
pip install -e ".[full]"
log init              # create vault at ~/.log/vault/
log dehydrate "Your text here"
```

### Cloudflare Workers

Free tier includes 100k requests/day, D1 database, and KV cache.

```bash
cd cloudflare
npm install
npx wrangler login
npx wrangler deploy
```

Endpoints: `/dehydrate`, `/rehydrate`, `/stats`, `/health`

Live demo: [https://log-mcp-vault.magnus-digennaro.workers.dev/](https://log-mcp-vault.magnus-digennaro.workers.dev/)

### Docker

```bash
docker build -t log-mcp .
docker run -p 8000:8000 -v log-vault:/data log-mcp
```

---

## PII Detection

LOG-mcp identifies and replaces these entity types:

| Entity | Example Input | Anonymized Output |
|---|---|---|
| Emails | `user@example.com` | `EMAIL_1` |
| Phone numbers | `+1 (555) 123-4567` | `PHONE_1` |
| SSNs | `123-45-6789` | `SSN_1` |
| Credit cards | `4532-1234-5678-9010` | `CC_1` |
| Names (English) | `Jane Marie Smith` | `ENTITY_1` |
| Addresses | `123 Main St, Springfield IL` | `ADDR_1` |
| Dates of birth | `1985-03-12` | `[DOB]` |
| Passport numbers | `US12345678` | `PASSPORT_1` |
| API keys | `sk-proj-abc123...` | `KEY_1` |
| Non-ASCII PII | Cyrillic/CJK names & data | Redacted |

---

## CLI Reference

| Command | Description |
|---|---|
| `log dehydrate "<text>"` | Strip PII, return anonymized text + session key |
| `log rehydrate <session-id>` | Restore original text from vault |
| `log init` | Initialize local vault (`~/.log/vault/`) |
| `log stats` | Show vault statistics (sessions, entities, storage) |
| `log scout <provider> "<text>"` | Dehydrate → send to AI → rehydrate response |
| `log archive <session-id>` | Archive a session to long-term storage |

---

## MCP Integration

Use LOG-mcp as an [MCP](https://modelcontextprotocol.io/) server:

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

Tools exposed: `dehydrate`, `rehydrate`, `stats`, `list_sessions`.

---

## Testing

```bash
# Unit tests (52 tests)
pytest tests/ -v

# E2e scenario suite (46 checks: HIPAA, legal, financial, multi-agent)
pytest tests/demo_e2e.py -v

# With coverage
pytest --cov=vault --cov=mcp --cov=scouts
```

---

## Project Links

| | |
|---|---|
| 🚀 **[Quickstart Guide](QUICKSTART.md)** | Get running in 5 minutes |
| 🗺️ **[Roadmap](ROADMAP.md)** | What's coming next |
| 🤝 **[Contributing](CONTRIBUTING.md)** | Join the project |
| 📄 **[License](LICENSE)** | MIT |

---

## License

MIT — use it however you want. Star the repo if it saves you from a compliance headache.
