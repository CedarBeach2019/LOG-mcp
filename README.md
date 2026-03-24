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

- NVIDIA Jetson Orin Nano 8GB (recommended sweet spot)
- Docker + Docker Compose
- Cloudflare account (free tier works)
- Python 3.10+

### 1. Clone and Initialize

```bash
git clone https://github.com/CedarBeach2019/LOG-mcp.git
cd LOG-mcp

# Initialize the Vault
./scripts/vault-init.sh
```

### 2. Start Local Services

```bash
# Pull and run the local redactor + SQLite RealLog
cd vault && docker-compose up -d
```

### 3. Connect to Cloudflare (Optional)

```bash
# Install and authenticate wrangler
npm install -g wrangler
wrangler login

# Deploy your Ghost portal
cd ghost && wrangler deploy --name log-gateway

# Map custom domain in Cloudflare Dashboard:
# Workers & Pages → Custom Domains → (yourname).log.ai
```

### 4. Test Dehydration

```bash
# The Vault CLI
pip install -e ./vault/cli
log dehydrate "Email my lawyer Sarah at sarah@firm.com"
# → "Email my lawyer <ENTITY_1> at <EMAIL_1>"
```

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

### Hot Memory (Live)

| Tool | Description |
|---|---|
| `log_dehydrate` | Strip PII from text, return safe pseudonymized version |
| `log_rehydrate` | Swap `LOG_ID` placeholders back to real values |

### Warm Memory (Context)

| Tool | Description |
|---|---|
| `log_distill` | Summarize conversation into semantic "Working-Fiction" |
| `log_ghost_sync` | Push dehydrated context to cloud Ghost portal |

### Cold Memory (Archive)

| Tool | Description |
|---|---|
| `log_prune_hysteresis` | GC: move memories Hot→Warm→Cold, prune stale data |
| `log_archive_gnosis` | Extract permanent lessons from completed sessions |

### Orchestration (Permissions)

| Tool | Description |
|---|---|
| `log_request_scout` | Dispatch dehydrated prompt to external agent |
| `log_issue_report` | Send quiet report without notification |

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
