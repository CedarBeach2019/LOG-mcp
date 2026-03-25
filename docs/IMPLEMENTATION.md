# IMPLEMENTATION.md — Low-Level Design

*Concrete blueprint for writing code. This is what aider executes against.*

---

## 1. Target File Structure

```
vault/
├── core.py            # Dehydrator/Rehydrator + RealLog (existing, refactored)
├── reallog_db.py      # Schema/migration support (existing, keep)
├── llm_scorer.py      # Ollama PII detection (existing, keep)
├── cli.py             # CLI interface (existing, extend with new subcommands)
├── archiver.py        # Filesystem archive logic (existing, keep)
├── router.py          # NEW: Complexity scorer + local/cloud routing decision
├── memory.py          # NEW: Embedding store, FTS5/vec retrieval, summarization
├── config.py          # NEW: Pydantic settings from .env (replaces ad-hoc env reads)
├── __init__.py        # Package exports
gateway/
├── __init__.py
├── server.py          # NEW: FastAPI/Starlette HTTP+WS server (aiohttp alternative: too heavy)
├── auth.py            # NEW: JWT issue/verify, passphrase hashing
├── routes.py          # NEW: HTTP route handlers (/v1/chat, /stats, /ws)
└── deps.py            # NEW: Dependency injection (RealLog instance, etc.)
tunnel/
├── __init__.py
└── manager.py         # NEW: cloudflared subprocess lifecycle, health pings
trainer/
├── __init__.py
└── pipeline.py        # NEW: Training queue consumer, LoRA fine-tune, adapter versioning
web/
├── index.html         # NEW: Single-file SPA (vanilla JS, no build step)
├── style.css          # Minimal styling
└── app.js             # WebSocket client, message rendering
docker/
├── Dockerfile         # Existing (update base image, add new deps)
├── docker-compose.yml # Rewrite for new architecture
└── .env.example       # Template
cloudflare/
├── worker/index.js    # Refactor: thin tunnel endpoint
├── wrangler.toml      # Existing (update bindings)
└── pages/             # Deleted — web/ served locally instead
tests/
├── test_core.py       # Existing
├── test_cli_db.py     # Existing
├── test_extended.py   # Existing
├── test_llm_scorer.py # Existing
├── test_router.py     # NEW
├── test_gateway.py    # NEW
├── test_memory.py     # NEW
├── conftest.py        # NEW: Shared fixtures (tmp vault, test client)
└── demo_e2e.py        # Existing
docs/
├── VISION.md
├── ARCHITECTURE.md
├── ROADMAP-v2.md
└── IMPLEMENTATION.md  # This file
```

---

## 2. Module Interfaces

### `vault/config.py` (NEW)
```python
class VaultSettings(BaseSettings):
    db_path: Path = Path("~/.log/vault/reallog.db")
    encryption_key: str | None = None          # Derived from passphrase if unset
    ollama_host: str = "http://localhost:11434"
    local_model: str = "phi3:mini"
    local_threshold: float = 0.7
    cloud_fallback: bool = True
    provider_endpoint: str = "https://api.openai.com/v1/chat/completions"
    api_key: str | None = None                  # CF Worker secret, not stored here
    tunnel_token: str | None = None
    enable_training: bool = False
    model_config = SettingsConfigDict(env_prefix="LOG_")
```

### `vault/core.py` — What Changes
**Stays:** `PIIEntity`, `Session`, `Message` dataclasses. `RealLog` class (all CRUD). `Dehydrator` + `Rehydrator`. Thread-safety lock.

**Changes:**
- `RealLog.__init__` accepts `VaultSettings` instead of raw `db_path` string. Reads `encryption_key` → opens with SQLCipher if set, plain SQLite otherwise.
- Add `_init_new_tables()` method that creates `training_queue`, `model_versions`, `user_preferences` tables (version 2 migration in `reallog_db.py`).
- Remove duplicate schema init between `RealLog._init_db()` and `RealLogDB._run_migration()` — `RealLog` should delegate to `RealLogDB.init_db()` only.
- `Dehydrator.dehydrate()` adds optional `session_id` param to scope entities per session (for future cleanup).

### `vault/router.py` (NEW)
```python
@dataclass
class RoutingDecision:
    target: Literal["local", "cloud", "reject"]
    confidence: float
    reason: str

class RequestRouter:
    def __init__(self, settings: VaultSettings, reallog: RealLog): ...
    async def route(self, messages: list[dict]) -> RoutingDecision:
        """Score request complexity via Ollama. Return routing decision."""
        # Sends system prompt: "Rate complexity 0-1. Consider: code generation,
        # reasoning depth, factual recall, creative writing."
        # If score >= threshold → local. Else → cloud. Always cloud if Ollama down.
    async def route_simple(self, messages: list[dict]) -> RoutingDecision:
        """Heuristic fallback (no LLM call). Token count, keyword detection."""
```

### `vault/memory.py` (NEW)
```python
class MemoryStore:
    def __init__(self, reallog: RealLog, ollama_host: str): ...
    async def embed(self, text: str) -> list[float]:
        """Get embedding via Ollama /api/embed (all-MiniLM-L6-v2 or nomic-embed-text)."""
    async def store(self, session_id: str, message: Message) -> None:
        """Embed message content, store in sqlite-vec virtual table."""
    async def retrieve(self, query: str, limit: int = 5) -> list[dict]:
        """Hybrid search: FTS5 keyword match + vector cosine similarity. Merge + rerank."""
    async def summarize_session(self, session_id: str) -> str:
        """Send messages to Ollama for rolling summary. Store in sessions.summary."""
```

### `trainer/pipeline.py` (NEW)
```python
class TrainingPipeline:
    def __init__(self, settings: VaultSettings, reallog: RealLog): ...
    async def enqueue(self, request: str, response: str, provider: str, quality: float) -> int:
        """Insert into training_queue. Return queue ID."""
    async def run_next_job(self) -> str | None:
        """Pop next pending job. Run LoRA fine-tune via unsloth CLI or Ollama API.
        Return new adapter version_id or None if queue empty."""
    async def promote_adapter(self, version_id: str) -> bool:
        """Set is_active=1 on new adapter, test with sample queries, rollback if degraded."""
```

### `gateway/server.py` (NEW)
```python
app = Starlette()  # Lightweight, async, WS support built-in

# HTTP routes
@app.route("/v1/chat")
async def chat(request): ...
@app.route("/v1/chat/completions")  # OpenAI-compat
async def chat_completions(request): ...
@app.route("/stats")
async def stats(request): ...
@app.route("/auth/login", methods=["POST"])
async def login(request): ...

# WebSocket
@app.websocket_route("/ws")
async def ws_chat(websocket): ...
    # Receive JSON: {"type": "message", "content": "...", "session_id": "..."}
    # Send JSON: {"type": "response", "content": "...", "model": "local|cloud"}
    # Dehydrate incoming, route, get response, rehydrate, send.
```

### `tunnel/manager.py` (NEW)
```python
class TunnelManager:
    def __init__(self, token: str, local_port: int = 8000): ...
    async def start(self) -> None:
        """Launch `cloudflared tunnel run` subprocess. Health-check loop."""
    async def stop(self) -> None:
        """Graceful shutdown. SIGTERM, wait, SIGKILL."""
    @property
    def status(self) -> str:
        """Return 'running'|'stopped'|'error'."""
    async def get_public_url(self) -> str | None:
        """Parse cloudflared stdout for tunnel URL."""
```

---

## 3. Worker Refactor Plan

**REMOVE from Worker:**
- All PII detection (`detectNames`, `PII_PATTERNS`, `COMMON_NON_NAMES`)
- `dehydrate()` and `rehydrate()` functions
- `storeMappings()` — no more D1 writes for PII
- D1 schema init (`ensureD1Schema`)
- KV usage (`PII_MAP.put/get`)
- `handleProxy` logic — Worker no longer touches message content
- `/dehydrate` and `/rehydrate` test endpoints

**ADD to Worker:**
- Request forwarding to tunnel URL: `fetch("http://localhost:8000/v1/chat", ...)`
- JWT verification middleware (read `AUTH_SECRET` env, verify token from `Authorization` header)
- WebSocket upgrade passthrough to tunnel (`/ws`)
- Static file serving for `web/index.html` (or serve from Pages, Worker handles API only)
- Rate limiting (CF-native, minimal code)

**STAYS:**
- CORS headers
- Health check (`/`)
- Basic error formatting (`json()` helper)
- `handleStats` → forward to `localhost:8000/stats`

**Target Worker outline:**
```javascript
export default {
  async fetch(request, env) {
    const TUNNEL = env.TUNNEL_URL;  // e.g. http://vault:8000
    // CORS handling (stays)
    // JWT verify middleware (new)
    // Route matching:
    //   GET / → health
    //   POST /auth/login → proxy to tunnel, or handle locally with D1
    //   POST /v1/chat, /v1/chat/completions → proxy to tunnel
    //   GET /ws → WebSocket upgrade, pipe to tunnel
    //   GET /stats → proxy to tunnel
    //   * → 404
  }
};
```

Worker becomes ~80 lines. All intelligence is local.

---

## 4. Chat UI Design

**Single HTML file.** No framework, no build step. Inline CSS + JS. One file to copy, one file to serve.

**Components:**
- Login screen: passphrase input → JWT stored in sessionStorage
- Chat area: scrollable message list, auto-scroll to bottom
- Input bar: textarea with Enter-to-send, Shift+Enter for newline
- Status bar: "Local" / "Cloud" / "Connecting..." indicator
- Settings modal: model preference, clear history

**WebSocket protocol:**
```
Client → Server:
  {"type": "message", "content": "Hello", "session_id": "abc123"}

Server → Client:
  {"type": "response", "content": "Hi there", "model": "local", "latency_ms": 180}
  {"type": "error", "message": "Ollama unavailable, falling back to cloud"}
  {"type": "routing", "decision": "cloud", "confidence": 0.4}
```

**Connection:** `wss://gateway.example.com/ws?token=<jwt>`. Reconnect with exponential backoff. Show connection status in UI.

**Styling:** Dark theme. Monospace code blocks. Max-width 720px centered. Mobile-friendly (viewport meta, responsive padding).

---

## 5. Docker Compose v2

```yaml
version: "3.9"
services:
  vault:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    ports: ["8000:8000"]
    env_file: ../.env
    volumes:
      - vault-data:/data
      - ../web:/app/web:ro        # Serve chat UI
    depends_on:
      ollama:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/"]
      interval: 15s

  ollama:
    image: ollama/ollama:latest
    ports: ["11434:11434"]
    volumes: [ollama-data:/root/.ollama]
    profiles: [local-llm]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434/"]

  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    profiles: [tunnel]
    command: tunnel --no-autoupdate run
    environment:
      TUNNEL_TOKEN: ${CF_TUNNEL_TOKEN}
    depends_on:
      vault:
        condition: service_healthy

volumes:
  vault-data:
  ollama-data:
```

**Changes from current:** `vault` service now includes the gateway server (Starlette). Ollama is no longer optional for `vault` health (gateway starts regardless, routing gracefully degrades). `cloudflared` added as proper service with dependency. Web UI served from vault container directly.

---

## 6. Migration Plan

### Phase 0 (do now, no breaking changes):
1. Create `vault/config.py` — Pydantic settings class. Existing code reads env vars directly; add config as optional param, fall back to env.
2. Deduplicate schema init — `RealLog` delegates to `RealLogDB`.
3. Add `reallog_db.py` migration v2 for new tables (training_queue, model_versions, user_preferences). Existing tables untouched.
4. Create `gateway/server.py` with `/v1/chat` endpoint that wraps existing `Dehydrator` + upstream proxy. This is the Worker's replacement but runs locally.
5. Write tests for the new gateway.

### What waits:
- Worker refactor (Phase 1) — after gateway works locally
- Router, Memory, Trainer (Phases 2-4) — after gateway is stable
- Removing PII from Worker — after local gateway proves it handles all cases

### Compatibility shims:
- Worker continues to work independently during Phase 0. Both Worker and local gateway can coexist. Worker can optionally proxy to local gateway instead of upstream API directly (add `TUNNEL_URL` env to Worker).

---

## 7. Test Strategy

**Unit tests (existing pattern with pytest, tmp_path fixtures):**
- `test_core.py` — Dehydrate/rehydrate round-trips, entity dedup, thread safety
- `test_router.py` — Mock Ollama responses, verify routing decisions
- `test_memory.py` — Embed/retrieve with in-memory SQLite + mock embeddings
- `test_gateway.py` — `httpx.AsyncClient` with Starlette `TestClient`

**Integration tests:**
- `tests/conftest.py` — fixture that starts Starlette app + tmp vault, provides test client
- `test_gateway.py` includes: dehydrate→route→respond→rehydrate full flow
- Test tunnel: mock `cloudflared` binary, verify `TunnelManager` subprocess lifecycle

**PII leakage tests (critical):**
- Send text with PII through gateway, capture what goes to upstream. Assert no real PII in forwarded request.
- Test with regex PII + LLM-detected PII.
- Test rehydration: verify response contains original values, not tokens.

---

## 8. Immediate Next Steps (Phase 0 Execution Order)

1. **Create `vault/config.py`** — Pydantic `VaultSettings` class. ~30 min.
2. **Create `tests/conftest.py`** — Shared fixtures: tmp RealLog, tmp vault path. ~15 min.
3. **Refactor `vault/core.py`** — Make `RealLog.__init__` accept optional `VaultSettings`. Delegate schema init to `RealLogDB`. ~1 hr.
4. **Add migration v2 in `vault/reallog_db.py`** — `training_queue`, `model_versions`, `user_preferences` tables. ~30 min.
5. **Create `gateway/server.py`** — Starlette app with `/v1/chat/completions` (OpenAI-compat proxy through Dehydrator). Serve `web/index.html` at `/`. ~2 hr.
6. **Create `gateway/routes.py`** — Extract route handlers from server.py. `gateway/auth.py` — JWT stub (hardcoded secret for now). ~1 hr.
7. **Create `web/index.html`** — Single-file chat UI with WebSocket. ~2 hr.
8. **Create `tests/test_gateway.py`** — Full dehydrate→proxy→rehydrate flow test. PII leakage assertion. ~1 hr.
9. **Update `docker/Dockerfile`** — Install `starlette`, `uvicorn`, `pydantic-settings`. Serve web/ directory. ~30 min.
10. **Update `docker/docker-compose.yml`** — New v2 config from §5. ~15 min.
11. **Run full test suite, fix breakage.** ~1 hr.

**Total Phase 0 estimate: ~9 hours.**
