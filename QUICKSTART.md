# 🚀 Quickstart — 5 Minutes to Your First PII Scrub

This guide walks you through getting LOG-mcp running, step by step. No prior experience with privacy tools needed.

## What you'll need

- **Python 3.10 or later** — that's it. Check with:
  ```bash
  python3 --version
  ```
  If you don't have Python, install it from [python.org](https://www.python.org/downloads/).

Pick **one** path below (Local, Cloudflare, or Docker) based on what you want to do.

---

## Path 1: Local Install (Recommended for trying it out)

This runs everything on your machine. Fastest to set up, zero network dependencies.

### Step 1 — Clone the repo

```bash
# This downloads the project code to your computer
git clone https://github.com/CedarBeach2019/LOG-mcp.git

# Move into the project directory
cd LOG-mcp
```

### Step 2 — Install

```bash
# Install LOG-mcp and its dependencies into your Python environment
pip install -e .
```

> **What's happening?** `pip install` downloads the required libraries (HTTP client, CLI framework). The `-e` flag means "editable" — changes you make to the code take effect immediately without reinstalling.

### Step 3 — Initialize your vault

```bash
log init
```

Expected output:
```
🪵 Initializing L.O.G. Vault...
✅ Created directory structure:
   /home/you/.log/vault/
   /home/you/.log/vault/archives/{shorts,sessions,gnosis}
✅ RealLog database initialized.
```

> **What's happening?** This creates a local SQLite database at `~/.log/vault/` that stores the mapping between anonymized tokens (like `ENTITY_1`) and real data. Your data never leaves your machine.

### Step 4 — Scrub some PII

```bash
log dehydrate "Contact Dr. Sarah Chen at sarah.chen@hospital.org, phone 415-555-0199. Patient MRN: 1234567890123"
```

Expected output:
```
Dehydrated: "Contact ENTITY_1 at EMAIL_1, phone PHONE_1. Patient MRN: REDACTED_1"
Session: sess_a1b2c3d4
```

> **What just happened?**
> - `Dr. Sarah Chen` was detected as a name → replaced with `ENTITY_1`
> - `sarah.chen@hospital.org` was detected as an email → replaced with `EMAIL_1`
> - `415-555-0199` was detected as a phone number → replaced with `PHONE_1`
> - The MRN was detected as a sensitive identifier → replaced with `REDACTED_1`
> - The mapping is stored in your local vault under session `sess_a1b2c3d4`

### Step 5 — Get the original back

```bash
log rehydrate sess_a1b2c3d4
```

Expected output:
```
Original: "Contact Dr. Sarah Chen at sarah.chen@hospital.org, phone 415-555-0199. Patient MRN: 1234567890123"
```

> **What's happening?** LOG-mcp looks up the session in your vault and reconstructs the original text. Only someone with access to your vault can do this — not the AI, not us, nobody.

🎉 **You're done!** You just anonymized sensitive data and restored it. That's the core loop.

---

## Path 2: Cloudflare Workers (Serverless, Free)

Best if you want a hosted API endpoint without managing a server. Free tier: 100k requests/day.

### Step 1 — Fork the repo

1. Go to [https://github.com/CedarBeach2019/LOG-mcp](https://github.com/CedarBeach2019/LOG-mcp)
2. Click **Fork** (top-right) to copy it to your GitHub account

### Step 2 — Install Cloudflare tools

```bash
# Install Node.js if you don't have it (from nodejs.org)
npm install -g wrangler
wrangler login
```

> **What's happening?** `wrangler` is Cloudflare's CLI tool. `wrangler login` opens your browser to authenticate with your Cloudflare account (free to create).

### Step 3 — Deploy

```bash
cd cloudflare
npm install
npx wrangler deploy
```

Expected output:
```
✨ Successfully published your script to
https://log-mcp-your-name.your-subdomain.workers.dev
```

### Step 4 — Test it

```bash
curl https://log-mcp-your-name.your-subdomain.workers.dev/dehydrate \
  -H "Content-Type: application/json" \
  -d '{"text": "Email john@acme.com or call 555-1234"}'
```

Expected response:
```json
{
  "dehydrated": "Email EMAIL_1 or call PHONE_1",
  "session_id": "sess_x9y8z7"
}
```

> **What just happened?** Your Worker received the text, stripped the PII, stored the mapping in a Cloudflare D1 database, and returned the anonymized version. The PII lives only in your D1 database — never in the AI's logs.

---

## Path 3: Docker (Self-Hosted)

Best for air-gapped environments, on-prem deployments, or if you just love containers.

### Step 1 — Build and run

```bash
docker build -t log-mcp https://github.com/CedarBeach2019/LOG-mcp.git
docker run -p 8000:8000 -v log-vault:/data log-mcp
```

Expected output:
```
🪵 LOG-mcp server starting on port 8000
✅ Vault initialized at /data/vault/
```

### Step 2 — Test it

```bash
curl http://localhost:8000/dehydrate \
  -H "Content-Type: application/json" \
  -d '{"text": "SSN 078-05-1120 belongs to Jane Public"}'
```

Expected response:
```json
{
  "dehydrated": "SSN SSN_1 belongs to ENTITY_1",
  "session_id": "sess_d4e5f6"
}
```

> **What's happening?** The `-v log-vault:/data` flag creates a persistent volume so your vault data survives container restarts. The PII mapping is stored locally in the container's data directory.

---

## Troubleshooting

| Problem | Solution |
|---|---|
| `log: command not found` | Run `pip install -e .` again. Make sure you're using the right Python (`which python3`). |
| `ModuleNotFoundError` | You're probably in the wrong directory. `cd LOG-mcp` first, then retry. |
| `wrangler login` doesn't open a browser | Run `wrangler login` with the `--browser` flag: `wrangler login --browser manual`. It'll give you a URL to paste. |
| Docker build fails | Ensure Docker is running (`docker info`). If on ARM (Mac M1/M2), the build should work — the image is multi-platform. |
| PII not being detected | Check that your input matches common formats. Names require capitalized first/last names (e.g., "Jane Smith", not "jane smith"). |

---

## Next Steps

Now that you're running:

- 📖 **[README.md](README.md)** — Full feature overview, all deployment modes, CLI reference
- 🔌 **[MCP Integration](README.md#mcp-integration)** — Use LOG-mcp with Claude Desktop, Cursor, or any MCP client
- 🤖 **[Scout Connectors](README.md#deployment-modes)** — Send anonymized text directly to Claude, GPT, or DeepSeek
- 🗺️ **[Roadmap](ROADMAP.md)** — See what's planned (batch processing, UI dashboard, more PII types)
- 🤝 **[Contributing](CONTRIBUTING.md)** — Add features, fix bugs, improve detection patterns
