# 🚀 Quickstart — 5 Minutes to Chat

LOG-mcp is a privacy-first chat gateway that routes your messages between local and cloud LLMs, scrubs PII, and gives you control over every request.

## What you'll need

- **Docker** (easiest) — [get.docker.com](https://docs.docker.com/get-docker/)
- **Or Python 3.10+** — check with `python3 --version`

---

## Option 1: Docker (Recommended)

Three commands from clone to running.

### Step 1 — Clone the repo

```bash
git clone https://github.com/CedarBeach2019/LOG-mcp.git
cd LOG-mcp
```

### Step 2 — Configure environment

```bash
cp docker/.env.example docker/.env
```

Edit `docker/.env` with your API key:

```env
LOG_API_KEY=sk-your-deepseek-key
LOG_PASSPHRASE=your-secret-password
```

### Step 3 — Launch

```bash
docker compose -f docker/docker-compose.yml up -d
```

🎉 Open **http://localhost:8000** — log in with your passphrase and start chatting.

---

## Option 2: Python (No Docker)

### Step 1 — Clone and install

```bash
git clone https://github.com/CedarBeach2019/LOG-mcp.git
cd LOG-mcp
pip install -e .
```

### Step 2 — Configure

```bash
export LOG_API_KEY=sk-your-deepseek-key
export LOG_PASSPHRASE=your-secret-password
```

### Step 3 — Run

```bash
log init
log serve
```

🎉 Open **http://localhost:8000** — log in and chat.

---

## First Session

1. **Open** http://localhost:8000 in your browser
2. **Log in** with the passphrase you set in `LOG_PASSPHRASE`
3. **Send a message** — type anything, e.g. `What is 2 + 2?`
4. **Try draft mode** — type `/draft Write a haiku about debugging`

The system auto-routes simple questions to a fast/cheap model and complex requests (code, analysis, long messages) to a reasoning model. You can override this with slash commands.

---

## Slash Commands

| Command | What it does |
|---------|-------------|
| `/local` | Force routing to your local Ollama model |
| `/cloud` | Use the cheap/fast cloud model |
| `/reason` | Use the heavy reasoning model (DeepSeek-Reasoner) |
| `/compare` | Run both cheap and reasoning models side-by-side |
| `/draft` | Get multiple concise rewrites (precise, creative, deep) |

Example: `/reason Explain quantum entanglement to a 10-year-old`

---

## Environment Variables

All settings use the `LOG_` prefix. Set them in your `.env` file or export directly.

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_API_KEY` | *(none)* | Cloud LLM API key (DeepSeek, OpenAI, etc.) |
| `LOG_PROVIDER_ENDPOINT` | DeepSeek chat endpoint | Main cloud model endpoint |
| `LOG_PASSPHRASE` | `changeme` | Password for the chat UI login |
| `LOG_LOCAL_PORT` | `8000` | Port for the web server |
| `LOG_CHEAP_MODEL_ENDPOINT` | DeepSeek chat endpoint | Fast model endpoint |
| `LOG_CHEAP_MODEL_NAME` | `deepseek-chat` | Fast model name |
| `LOG_ESCALATION_MODEL_ENDPOINT` | DeepSeek chat endpoint | Reasoning model endpoint |
| `LOG_ESCALATION_MODEL_NAME` | `deepseek-reasoner` | Reasoning model name |
| `LOG_OLLAMA_BASE_URL` | `http://localhost:11434` | Local Ollama server URL |
| `LOG_ROUTER_MODEL` | `qwen3.5:2b` | Local routing/classification model |
| `LOG_PRIVACY_MODE` | `true` | Enable PII scrubbing before sending to cloud |
| `LOG_DRAFT_MODE` | `true` | Enable the `/draft` multi-rewrite feature |
| `LOG_RATE_LIMIT` | `30` | Max requests per minute |
| `LOG_DB_PATH` | `~/.log/vault/reallog.db` | SQLite vault location |

---

## Browser Access

Once running, open **http://localhost:8000** in any browser. The chat UI supports:

- Real-time streaming responses
- Slash commands (type `/` to see options)
- Message history (stored in your local vault)

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| Can't reach localhost:8000 | Check Docker is running: `docker ps`. Verify the port isn't in use. |
| `log: command not found` | Run `pip install -e .` and ensure your Python bin is in `$PATH`. |
| Local model errors | Install Ollama and pull a model: `ollama pull qwen3.5:2b` |
| Cloud model errors | Check `LOG_API_KEY` is set and valid. |

---

## Next Steps

- 📖 **[README.md](README.md)** — Full feature overview and architecture
- 🔌 **[MCP Integration](README.md#mcp-integration)** — Use with Claude Desktop, Cursor, or any MCP client
- 🔒 **[Privacy](README.md#privacy)** — How PII scrubbing and the vault work
- 🗺️ **[Roadmap](ROADMAP.md)** — What's planned next
- 🤝 **[Contributing](CONTRIBUTING.md)** — Bug reports, features, PRs welcome
