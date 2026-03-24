# 🪵 L.O.G. — Latent Orchestration Gateway

> **Your privacy-first memory layer for the agentic era.**

L.O.G. is a self-hosted system that sits between you and any AI agent. It creates a secure "hysteresis" — a historical lag — between your private data and the agents you use. Your real data never leaves your hardware. Agents only ever see a "Working-Fiction" — pseudonymized, distilled, and safe.

## The Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    THE VAULT (Local)                     │
│         NVIDIA Jetson — Your sovereign territory         │
│                                                          │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐  │
│  │ RealLog  │  │ Redactor │  │ Hysteresis Pruner     │  │
│  │ (SQLite) │←→│ (Local   │←→│ (Garbage Collector    │  │
│  │ PII map  │  │  LLM)    │  │  Hot→Warm→Cold)       │  │
│  └──────────┘  └──────────┘  └───────────────────────┘  │
│         ↕              ↕                                │
│  ┌──────────┐  ┌──────────┐                            │
│  │ Archive  │  │ Summary  │                            │
│  │ (Full    │  │ Index    │                            │
│  │  Text)   │  │          │                            │
│  └──────────┘  └──────────┘                            │
└──────────────────────┬──────────────────────────────────┘
                       │ Cloudflare Tunnel (zero exposed ports)
                       ↓
┌─────────────────────────────────────────────────────────┐
│                  THE GHOST (Cloud)                       │
│       (username).log.ai — Your public-facing proxy      │
│                                                          │
│  ┌──────────┐  ┌──────────┐                             │
│  │ Working  │  │ Approval │                             │
│  │ Fiction  │  │ Gate     │                             │
│  │ (Dehyd.) │  │ (Risk    │                             │
│  │          │  │  Check)  │                             │
│  └──────────┘  └──────────┘                             │
└──────────────────────┬──────────────────────────────────┘
                       │ Dehydrated prompts only
                       ↓
┌─────────────────────────────────────────────────────────┐
│                   THE SCOUTS (External)                  │
│    Claude · DeepSeek · Devin · Manus · Any Agent        │
│                                                          │
│  Agents see pseudonyms. They never see your real data.   │
└─────────────────────────────────────────────────────────┘
```

## Vocabulary

| Term | Meaning |
|---|---|
| **Dehydration** | Strip PII, replace with `LOG_ID` placeholders before anything leaves the Vault |
| **Rehydration** | Swap `LOG_ID` back to real values when showing results to the human |
| **Working-Fiction** | The dehydrated version of your data that agents interact with |
| **Hysteresis Pruning** | Automated GC: Hot (local) → Warm (cloud vectors) → Cold (archived) → Pruned |
| **Gnosis** | Permanent lessons learned, extracted from completed interactions |
| **Approval Gate** | High-risk agent actions require human sign-off before execution |
| **The Vault** | Your local Jetson hardware — the only place real data lives |
| **The Ghost** | Your Cloudflare Worker — the dehydrated face you show the world |
| **The Scout** | Any external agent processing your dehydrated data |

## Quick Start

### Prerequisites

- Python 3.10+ (required for type hints and modern syntax)
- SQLite3 (usually included with Python)
- NVIDIA Jetson Orin Nano 8GB (recommended for local redaction) or any Linux machine
- Docker + Docker Compose (optional, for containerized redactor service)
- Cloudflare account (optional, for Ghost cloud deployment)

### 1. Clone and Initialize

```bash
git clone https://github.com/CedarBeach2019/LOG-mcp.git
cd LOG-mcp

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install core dependencies
pip install -e .

# Initialize the Vault database
python -m vault.cli init
```

### 2. Configure Environment

```bash
# Copy example environment file
cp .env.example .env
# Edit .env with your settings (API keys, paths, etc.)
```

### 3. Start Local Services (Optional)

If using the Docker-based redactor:

```bash
cd vault && docker-compose up -d
```

### 4. Test Core Functionality

```bash
# Test dehydration
python -m vault.cli dehydrate "Email my lawyer Sarah at sarah@firm.com"
# Expected output: "Email my lawyer <ENTITY_1> at <EMAIL_1>"

# Test rehydration
python -m vault.cli rehydrate "Email my lawyer <ENTITY_1> at <EMAIL_1>"
# Should restore original text if session exists

# Check system status
python -m vault.cli status
```

### 5. Connect to Cloudflare (Optional)

```bash
# Install wrangler CLI
npm install -g wrangler
wrangler login

# Deploy your Ghost portal (requires ghost/ directory)
cd ghost && wrangler deploy
```

## Environment Variables

See `.env.example` for all required and optional variables. Key variables include:

- `VAULT_DB_PATH`: Path to SQLite database
- `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`: For external scout connectors
- `REDACTOR_HOST`, `REDACTOR_PORT`: Local redactor service settings
- Cloudflare settings for Ghost deployment

## Target Hardware

**Primary: NVIDIA Jetson Orin Nano 8GB**
- 40 TOPS AI performance
- Runs local redactor models (Llama-3.2-1B, Qwen2.5-1.5B, Phi-3-mini)
- Handles PII detection, dehydration, hysteresis pruning
- Vision-capable variant available (Orin Nano with camera module)
- Headless operation — no display needed
- ~$250 USD — the sweet spot for price/performance

**Also supported:** Jetson Orin NX 16GB, AGX Orin 64GB, any Linux machine with Python 3.10+

## Project Structure

```
LOG-mcp/
├── vault/              # Local engine (The Vault)
│   ├── cli/            # Python CLI tool
│   ├── redactor/       # Local LLM redaction service
│   ├── archiver/       # Session archiver + summary engine
│   ├── pruner/         # Hysteresis garbage collector
│   ├── sqlite/         # RealLog database schemas
│   └── docker-compose.yml
├── ghost/              # Cloudflare Worker (The Ghost)
│   ├── worker/         # Worker source
│   ├── pages/          # Dashboard (React/Hono)
│   └── wrangler.toml
├── mcp/                # MCP Server (agent interface)
│   ├── server.py       # MCP tool definitions
│   └── transport/      # stdio + HTTP transports
├── scouts/             # Agent connectors
│   ├── claude.py
│   ├── deepseek.py
│   └── base.py
├── docs/               # Documentation
├── scripts/            # Setup and utility scripts
└── README.md           # This file
```

## MCP Tools (Agent Vocabulary)

The MCP server in `mcp/server.py` provides the following tools for agents:

### Hot Memory (Live)

| Tool | Description |
|---|---|
| `log_dehydrate` | Strip PII from text, return safe pseudonymized version |
| `log_rehydrate` | Swap `LOG_ID` placeholders back to real values |

### Archive & Search

| Tool | Description |
|---|---|
| `log_archive_session` | Archive a conversation session with metadata |
| `log_search_archives` | Search archived sessions by keyword |
| `log_archive_gnosis` | Extract permanent lessons from completed sessions |

### System Management

| Tool | Description |
|---|---|
| `log_prune_hysteresis` | Garbage collect old data based on hysteresis settings |
| `log_vault_status` | Check vault health and statistics |

> Note: Some tools mentioned in earlier documentation (like `log_distill`, `log_ghost_sync`, `log_request_scout`, `log_issue_report`) are planned but not yet implemented in the current version.

## Privacy Guarantee

> Your Jetson. Your Cloudflare account. Your data.
>
> The L.O.G. developers never see your data. Your `(username).log.ai` is sovereign territory. The Vault never opens ports. The Ghost never stores real names. The Scouts never see the RealLog.

## Philosophy

L.O.G. is built on one principle: **the gap between your thoughts and an agent's actions should contain a wall, not a pipe.**

Most systems route your raw data directly to cloud APIs. L.O.G. inserts a mandatory dehydration step — nothing leaves your hardware until it's been scrubbed clean. The "Working-Fiction" that agents see is useful enough to get the job done, but useless to anyone trying to reconstruct your identity.

The hysteresis pruning system ensures your data doesn't grow forever. Memories cool down naturally — from Hot (immediate) to Warm (searchable) to Cold (archived) to Gnosis (permanent lessons). When the cold storage hits your configured limit, the garbage collector prunes intelligently — keeping patterns, discarding raw text.

## Contributing

This is an open system. Fork it. Run it on your Jetson. Make it yours.

See [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) for guidelines.

## License

MIT
