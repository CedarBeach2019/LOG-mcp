# ARCHITECTURE.md — Personal AI Gateway

*Technical reference for implementation.*

---

## 1. System Components

### Cloudflare Worker (Edge Gateway)
The public-facing entry point. Receives chat requests, dehydrates PII via regex (names, emails, phones, SSNs, CCs, API keys, addresses), proxies to upstream AI provider, rehydrates responses. Currently a simple stateless proxy with D1-backed session storage and KV for fast PII mapping lookups (1hr TTL). Will evolve into the routing brain — deciding local vs cloud, managing auth, serving the chat UI.

### Cloudflare Tunnel (`cloudflared`)
Encrypted tunnel from Cloudflare's edge to your local machine. Eliminates need for port forwarding, static IPs, or open firewall ports. The Worker talks to `your-tunnel.cfargotunnel.com` which terminates at `localhost:8000` on your hardware. Token-based auth; the tunnel only allows traffic destined for configured local services.

### Local Agent Runtime (OpenClaw or generic)
The orchestrator running on your hardware. Manages sessions, coordinates between LOG-mcp core and Ollama, handles memory retrieval, and executes the self-improvement pipeline. Currently implicit (the vault is the agent); will become explicit as complexity grows. Must support MCP tool calls, background tasks, and health monitoring.

### LOG-mcp Core (PII Engine)
Python service (`vault/core.py`). Regex-based dehydration/rehydration with SQLite-backed entity vault. Maps real values → typed placeholders (`<ENTITY_1>`, `<EMAIL_2>`). Stores entity registry in local SQLite. Supports optional LLM-assisted PII detection via Ollama for contextual entities. This is the privacy boundary — nothing crosses it without tokenization.

### Ollama (Local Inference)
Local LLM runner. Handles inference for PII scoring (Phase 1), request routing classification (Phase 2), and eventually user-facing chat responses. Runs quantized models (Phi-3-mini, Gemma-2-2b, Llama) via llama.cpp backend. Exposes HTTP API at `localhost:11434`. GPU-accelerated on Jetson, CPU fallback on PCs.

### SQLite Vault (Data Layer)
Single-file encrypted database. Stores PII entity mappings, conversation sessions, messages, and (eventually) training queues and model versioning. Runs locally — never leaves the machine. SQLCipher or similar for at-rest encryption. FTS5 for text search, sqlite-vec for vector embeddings.

---

## 2. Data Flow Diagrams

### Happy Path: Local inference handles the request
```
User → CF Worker → [PII stripped] → CF Tunnel → Local Vault
  → Ollama (local LLM) → response → Rehydrate → User
```

### Cloud Fallback: Local can't handle it
```
User → CF Worker → [PII stripped] → CF Tunnel → Local Vault
  → Router: "too complex for local" → Cloud API (dehydrated)
  → response → Rehydrate locally → User
  → Response pair saved to training_queue
```

### Training Path: Cloud response improves local model
```
Cloud API response arrives → [redacted req, quality score] → training_queue (SQLite)
  → Background daemon picks up → LoRA fine-tune on Jetson
  → New adapter version saved → model_versions table updated
  → Router tests new version on sample queries → promote or rollback
```

### Auth Flow
```
User opens chat UI → CF Worker serves static page
  → User enters passphrase → hashed → compared to stored hash (D1)
  → Session token issued (JWT, 24hr expiry)
  → WebSocket upgraded with token → Tunnel → Local agent
  → All subsequent requests carry JWT
```

---

## 3. API Surface

### Cloudflare Worker (REST)
| Endpoint | Method | Purpose |
|---|---|---|
| `/` | GET | Health check |
| `/v1/chat/completions` | POST | Main chat proxy (OpenAI-compatible) |
| `/v1/chat` | POST | Gateway-native chat (routing, memory) |
| `/stats` | GET | Vault statistics |
| `/dehydrate` | GET | Test PII stripping |
| `/rehydrate` | GET | Test PII restoration |
| `/auth/login` | POST | Authenticate, get JWT |
| `/ws` | GET | WebSocket for streaming chat |

### Cloudflare Worker (WebSocket)
`/ws` — bidirectional streaming. Client sends JSON messages, server streams responses. PII stripping/rehydration happens transparently. Router decision (local vs cloud) is included in metadata.

### Local MCP Tools (stdio)
- `dehydrate(text)` → dehydrated text + entity list
- `rehydrate(text)` → restored text
- `store_entity(type, value)` → entity ID
- `get_entities()` → all registered entities
- `query_history(query, limit)` → semantic search over conversations
- `get_stats()` → vault statistics

### Internal (service-to-service)
- `POST localhost:11434/api/chat` — Ollama inference
- `POST localhost:8000/internal/route` — routing decision
- `POST localhost:8000/internal/train` — trigger fine-tuning job

---

## 4. Database Schema

### Existing Tables

**`pii_map`** — Entity registry. Maps real PII values to tokens.
```sql
entity_id TEXT PRIMARY KEY,   -- e.g. ENTITY_1, EMAIL_3
entity_type TEXT NOT NULL,    -- person|email|phone|address|ssn|cc|api_key
real_value TEXT NOT NULL,     -- The actual PII (encrypted at rest)
created_at TEXT, last_used TEXT
```

**`sessions`** — Conversation sessions.
```sql
id TEXT PRIMARY KEY, timestamp TEXT, summary TEXT, metadata TEXT (JSON)
```

**`messages`** — Individual messages within sessions.
```sql
id INTEGER PK, session_id FK→sessions, role TEXT, content TEXT, timestamp TEXT
```

### New Tables

**`training_queue`** — Pending fine-tuning examples.
```sql
id INTEGER PK AUTOINCREMENT,
request_text TEXT NOT NULL,        -- Redacted user request
response_text TEXT NOT NULL,       -- Model response (redacted)
provider TEXT,                     -- Which model produced it
quality_score REAL DEFAULT 0.5,    -- 0-1, user feedback or auto-scored
status TEXT DEFAULT 'pending',     -- pending|training|completed|rejected
created_at TEXT
```
*Purpose:* Queue of (request, response) pairs for local fine-tuning. Populated when cloud API responses are better than local ones.

**`model_versions`** — Tracked model adapters.
```sql
version_id TEXT PRIMARY KEY,       -- e.g. "phi3-adapter-v0.3"
base_model TEXT NOT NULL,          -- e.g. "phi3:mini"
adapter_path TEXT,                 -- Path to LoRA adapter weights
created_at TEXT,
metrics TEXT (JSON),               -- {f1: 0.92, latency_ms: 180, ...}
is_active INTEGER DEFAULT 0,       -- Currently loaded adapter
parent_version TEXT                -- For rollback chain
```
*Purpose:* Track which model adapter is active, enable rollback if quality degrades.

**`user_preferences`** — User-specific config.
```sql
key TEXT PRIMARY KEY, value TEXT, updated_at TEXT
```
*Purpose:* Store user name, timezone, routing preferences, privacy thresholds. Keeps config alongside data.

**`tunnel_config`** — Tunnel connection state.
```sql
tunnel_id TEXT PRIMARY KEY,
public_url TEXT,                   -- e.g. "https://my-gateway.cfargotunnel.com"
local_port INTEGER DEFAULT 8000,
status TEXT DEFAULT 'disconnected',
last_heartbeat TEXT
```
*Purpose:* Track tunnel state for health monitoring and multi-device routing.

---

## 5. Security Model

### Threat: Cloud API receives unredacted PII
**Mitigation:** PII dehydration happens at the edge (CF Worker) before any proxy. Local vault also validates dehydration completeness. Redundant check.

### Threat: Tunnel compromised / token leaked
**Mitigation:** mTLS between CF Tunnel and local service. Tunnel token rotates automatically. Local service validates `CF-Connecting-IP` header. Rate-limit tunnel connections. Tunnel config is scoped to specific local ports only.

### Threat: Vault database stolen from hardware
**Mitigation:** SQLCipher encryption at rest. Encryption key derived from user passphrase (Argon2id) — not stored on disk. Without the passphrase, the vault is unreadable.

### Threat: API key exposure
**Mitigation:** Cloud API keys stored as CF Worker secrets (never in code, never in D1). Local-only keys (Ollama doesn't need auth) are firewalled. Vault service binds to localhost only.

### Threat: Jetson physically compromised
**Mitigation:** Vault encrypted at rest (useless without passphrase). Tunnel token can be revoked from CF dashboard. API keys are in CF, not on device. Wipe command: `npx log-mcp wipe --confirm` securely deletes vault and training data. Full disk encryption recommended.

---

## 6. Deployment Topology

### Mode A: Cloudflare + Jetson (Full Experience)
- CF Worker (edge) ↔ CF Tunnel ↔ Jetson (vault + Ollama + agent)
- Local inference, cloud fallback, self-improvement, memory
- Requires: Jetson Orin 8GB+, CF account, tunnel token

### Mode B: Cloudflare + Any PC (Minimum Viable)
- CF Worker ↔ CF Tunnel ↔ PC (vault + CPU Ollama)
- Slower local inference, cloud does heavy lifting
- Requires: Any modern PC, 16GB RAM, CF account

### Mode C: Docker Only (Offline / Air-Gapped)
- No CF. Everything runs locally via `docker-compose up`.
- Vault + Ollama only. No cloud API, no tunnel.
- Requires: Docker, 16GB RAM, downloaded model weights
- Start with: `docker compose --profile local-llm up`

---

## 7. Configuration

### Environment Variables
```bash
# Core
LOG_VAULT_DB_PATH=~/.log/vault/reallog.db   # SQLite path
ENCRYPTION_KEY=                              # Derived from passphrase if unset

# Tunnel
CF_TUNNEL_TOKEN=                             # From CF dashboard
TUNNEL_LOCAL_PORT=8000

# Cloud API
API_KEY=                                     # Upstream provider key (CF Worker secret)
PROVIDER_ENDPOINT=https://api.openai.com/v1/chat/completions

# Local LLM
OLLAMA_HOST=http://ollama:11434              # Or http://localhost:11434
DEFAULT_MODEL=phi3:mini

# Routing
LOCAL_INFERENCE_THRESHOLD=0.7               # Confidence to try local first
CLOUD_FALLBACK=true                          # Allow cloud on local failure

# Training
ENABLE_TRAINING=false                        # Background fine-tuning
TRAINING_IDLE_SECONDS=300                    # Wait before training
MAX_TRAINING_JOBS_PER_DAY=3
```

**Auto-detected:** Ollama availability, GPU presence (CUDA/Jetson), available disk space, tunnel connectivity.
**Manual:** API keys, tunnel token, encryption passphrase.

---

## 8. Hardware Requirements

| Tier | Specs | Experience |
|---|---|---|
| **Minimum** | Any PC, 16GB RAM, 4-core CPU, 20GB disk | Cloud does most work. Local PII regex only. Ollama runs small models slowly. |
| **Recommended** | Jetson Orin Nano 8GB or PC w/ RTX 3060, 32GB RAM, 100GB SSD | Local handles 60-70% of requests. Fine-tuning possible. <500ms local latency. |
| **Ideal** | Jetson Orin NX 16GB or PC w/ RTX 4070+, 64GB RAM, 500GB NVMe | 90%+ local inference. Background training while serving. Fast adapter iteration. |
