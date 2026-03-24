# 🪵 L.O.G. — Latent Orchestration Gateway

> **Your data never leaves your machine. AI agents never see your real identity.**

L.O.G. is a privacy-first middleware layer that sits between you and any AI service. Before your messages reach Claude, GPT, DeepSeek, or any other API, L.O.G. strips out all personal information — names, emails, phone numbers, SSNs, credit cards, addresses — and replaces them with anonymous placeholders. The AI works with pseudonymized data. L.O.G. swaps the real values back before you see the response.

```
You write:  "Book a flight for Sarah Chen, email sarah@gmail.com, card 4111-2222-3333-4444"
L.O.G. sends: "Book a flight for <ENTITY_1>, email <EMAIL_1>, card <CC_1>"
AI sees:    Only the anonymized version — zero PII
You see:    "Flight booked for Sarah Chen, confirmation sent to sarah@gmail.com"
```

**Zero trust required in your AI provider. Your PII never touches their servers.**

## Why

Every time you use an AI assistant for something real — booking flights, managing legal documents, handling finances, medical questions — your personal data goes to a cloud server you don't control. You're trusting a Terms of Service with your SSN.

L.O.G. inserts a mandatory dehydration step. Nothing leaves your hardware until it's been scrubbed clean.

## How It Works

```
┌──────────────┐     ┌─────────────────┐     ┌──────────────┐
│   YOUR DATA  │────→│  L.O.G. VAULT   │────→│  AI SERVICE  │
│              │     │                 │     │              │
│  Sarah Chen  │     │  <ENTITY_1>     │     │  Only sees   │
│  sarah@...   │     │  <EMAIL_1>      │     │  anonymous   │
│  4111-2222…  │     │  <CC_1>         │     │  data        │
│              │     │                 │     │              │
│              │←────│  Rehydrate      │←────│  Response    │
│  Sarah Chen  │     │                 │     │  with IDs    │
└──────────────┘     └─────────────────┘     └──────────────┘
                          ↓
                   ┌──────────────┐
                   │ LOCAL VAULT  │
                   │ SQLite + AES │
                   │ Your machine │
                   │ Only         │
                   └──────────────┘
```

### Three Deployment Modes

| Mode | Where | Cost | Best For |
|------|-------|------|----------|
| **Cloud** | Cloudflare Workers | **Free** | Quick start, no local hardware |
| **Hybrid** | Cloudflare + Local (Jetson/PC) | **Free** | Smart redaction with local LLM |
| **Self-Hosted** | Docker container | **Free** | Full control, air-gapped, teams |

## Detects

- ✅ Email addresses (all formats including +aliases)
- ✅ Phone numbers (US, international)
- ✅ Social Security Numbers
- ✅ Credit/debit card numbers
- ✅ API keys and tokens (sk-, key_, etc.)
- ✅ Street addresses
- ✅ Passport numbers
- ✅ Personal names (context-aware, minimal false positives)
- ✅ Non-ASCII PII (Chinese phones, Russian names)

## Install

```bash
# Works on any machine with Python 3.10+
git clone https://github.com/CedarBeach2019/LOG-mcp.git
cd LOG-mcp
pip install -e .
log init

# Try it
echo "Email sarah@gmail.com, call 555-123-4567, SSN 000-00-0000" | log dehydrate --json
# → {"dehydrated": "<EMAIL_1>, call <PHONE_1>, SSN <SSN_1>", "entities": 3}
```

### CLI

```bash
log init                  # Initialize vault database
log dehydrate             # Strip PII from stdin or arguments
log rehydrate             # Restore real values from placeholders
log status                # Show vault statistics
log entities list         # List all stored PII mappings
log gnosis "Title" "Body" # Save a permanent lesson learned
log archive               # Archive a session
log search "query"        # Search archives
log prune                 # Run garbage collector
```

### MCP Server

Works with any MCP-compatible AI agent (Claude Desktop, OpenAI Codex, etc.):

```json
{
  "mcpServers": {
    "log-vault": {
      "command": "python",
      "args": ["mcp/server.py"],
      "cwd": "/path/to/LOG-mcp"
    }
  }
}
```

**MCP tools:** `log_dehydrate`, `log_rehydrate`, `log_vault_status`, `log_archive_session`, `log_archive_gnosis`, `log_search_archives`, `log_prune_hysteresis`

### Docker

```bash
cd LOG-mcp
docker compose -f docker/docker-compose.yml up -d
# Vault running at http://localhost:8000
# Health check at http://localhost:8000/health
```

### Cloudflare Workers (Free)

```bash
cd LOG-mCP/cloudflare/worker
npm install
# Set your API keys
echo "AI_API_KEY=your-key" > .dev.vars
echo "PROVIDER_ENDPOINT=https://api.deepseek.com/v1/chat/completions" >> .dev.vars

# Deploy
wrangler deploy
# Your privacy proxy is live at your-worker.workers.dev
```

**Or via GitHub Actions:** Fork → add secrets → push to main → auto-deploys.

## Architecture

```
LOG-mcp/
├── vault/                  # Core engine
│   ├── core.py            # RealLog DB, Dehydrator, Rehydrator
│   ├── archiver.py        # Session archiving + gnosis extraction
│   ├── cli.py             # CLI interface
│   └── reallog_db.py      # Database schema + migrations
├── mcp/
│   └── server.py          # MCP server (JSON-RPC over stdio)
├── scouts/                 # Agent connectors
│   ├── base.py            # Base scout interface
│   ├── claude.py          # Claude connector
│   └── deepseek_scout.py  # DeepSeek connector
├── cloudflare/
│   ├── worker/            # Cloudflare Worker (privacy proxy)
│   └── pages/             # Landing page + interactive demo
├── docker/
│   ├── Dockerfile         # Multi-stage container
│   └── docker-compose.yml # Full stack (vault + optional Ollama)
├── tests/
│   ├── test_core.py       # 7 unit tests
│   ├── test_extended.py   # 29 comprehensive tests
│   └── demo_e2e.py        # 7 end-to-end scenario demos
├── ROADMAP.md             # 8-phase development plan
└── pyproject.toml
```

## Roadmap Highlights

| Phase | What | Status |
|-------|------|--------|
| 0 | Regex PII, SQLite, MCP, CLI | ✅ Done |
| 1 | Local LLM redaction (Jetson GPU) | Next |
| 2 | Intelligent provider routing | Planned |
| 3 | Vector search + memory management | Planned |
| 4 | API rate limit optimization | Planned |
| 5 | Self-improving daemon (RL) | Planned |
| 6 | Multi-agent ecosystem (AutoClaw-inspired) | Planned |
| 7 | Autonomous intelligence | Planned |

See [ROADMAP.md](ROADMAP.md) for the full plan with technical details and timelines.

## Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
# 37 passed
```

Plus 46 end-to-end checks covering realistic scenarios: medical records (HIPAA), attorney-client privilege, financial data, multi-agent collaboration, multi-turn conversations.

## Under the Hood

- **Thread-safe** SQLite with proper locking for concurrent access
- **Persistent connection pooling** — no connection overhead per operation
- **44 dehydrations/sec** on Jetson Orin Nano (regex path)
- **Consistent entity IDs** across sessions — same email always gets the same placeholder
- **Atomic check-and-insert** prevents race conditions in multi-agent setups

## Privacy Guarantee

- Your data never leaves your machine unredacted
- No telemetry, no phone home, no analytics
- SQLite database is local — no cloud sync of real PII
- MIT licensed, fully auditable

## Contributing

Fork it. Run it. Break it. Fix it. Open a PR.

## License

MIT
