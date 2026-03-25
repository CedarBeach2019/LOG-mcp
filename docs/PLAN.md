# PLAN.md — Phase 0 Execution Plan

*Final plan for aider. No implementation code below — only what to build, in what order, and how to verify it.*

---

## Resolved Decisions

| Issue | Resolution |
|---|---|
| PII engine location | Local-only. Worker is DEPRECATED — keep it running but do not maintain. No new PII logic there. |
| Entity tokens | `[PERSON_A]`, `[EMAIL_B]`, `[PHONE_C]`, `[SSN_D]`, `[CC_E]`, `[ADDRESS_F]` — descriptive, typed, sequential. |
| Token coherence | System preamble prepended to every dehydrated message (see §Coherence Strategy). |
| Auth | JWT secret generated on first run via `secrets.token_urlsafe(32)`, stored in `user_preferences` table. |
| Rate limiting | `slowapi` on all `/v1/*` endpoints: 30 req/min, 10KB max body. |
| WebSocket auth | Token sent in first `{"type":"auth","token":"..."}` message, not URL. |
| DB changes | Phase 0 adds only `user_preferences`. No `training_queue` or `model_versions`. |
| Streaming | Not in Phase 0. HTTP POST only. |
| Vault encryption | Starts unencrypted. Warning printed on startup. CLI encrypt command deferred to Phase 1. |

---

## Coherence Strategy

Dehydrated text must remain meaningful to the LLM. Approach: **descriptive tokens + system preamble**.

**Token format:** `[PERSON_A]`, `[EMAIL_B]`, `[PHONE_C]`, `[SSN_D]`, `[CC_E]`, `[ADDRESS_F]`. Sequential per type within a session.

**Preamble** prepended to every request sent to cloud API:

```
Some personal information in this conversation has been replaced with tokens for privacy.
Tokens follow the format [TYPE_LETTER]. For example, [PERSON_A] refers to a specific
person whose name is not provided. [EMAIL_B] refers to a specific email address.
Treat each token as a unique, consistent entity throughout the conversation — the same
token always refers to the same real entity. Respond naturally as if you know who these
entities are, but never try to guess or fabricate their real values.
```

**Test:** Send "My friend John Smith (john@example.com) wants to meet for coffee. Should I go?" through dehydrator. Assert (1) no PII in forwarded text, (2) cloud response is coherent and references the friend naturally, (3) rehydrated response contains "John Smith" and "john@example.com".

This is the most important design decision. If coherence fails, the product fails.

---

## Phase 0 Scope

**What ships:** `docker compose up` → `localhost:8000` → chat UI → message dehydrated locally → proxied to cloud API → rehydrated → displayed.

**What does NOT ship:** Tunnel, local LLM, WebSocket streaming, training, memory, multi-device, encryption, Worker changes.

---

## Task List

### Task 1: Project config
**Files:** `vault/config.py`
**Do:** Create `VaultSettings(BaseSettings)` with: `db_path` (default `~/.log/vault/reallog.db`), `provider_endpoint`, `api_key`, `local_port` (8000), `rate_limit` (30/min), `max_body_bytes` (10240). Use `pydantic-settings`, env prefix `LOG_`.
**Acceptance:** Can instantiate with no args; env vars override defaults. Existing code unaffected.
**Depends on:** Nothing.

### Task 2: DB migration — user_preferences only
**Files:** `vault/reallog_db.py`
**Do:** Add migration v2 that creates `user_preferences` table: `key TEXT PK, value TEXT, updated_at TEXT`. Seed row: `("jwt_secret", generated-secret, now())` on first run.
**Acceptance:** `pytest tests/test_core.py` passes. New table exists in fresh DB.
**Depends on:** Task 1.

### Task 3: Entity token refactor
**Files:** `vault/core.py`
**Do:** Change `Dehydrator` to produce `[PERSON_A]`, `[EMAIL_B]`, etc. instead of `<ENTITY_1>`, `<EMAIL_2>`. Each entity type has its own letter counter (A, B, C...) per session. Update `Rehydrator` to reverse the new format. Add `_build_preamble()` method returning the coherence preamble string.
**Acceptance:** Existing dehydrate/rehydrate tests pass with updated expected tokens. New token format matches spec.
**Depends on:** Task 2.

### Task 4: Config integration in core
**Files:** `vault/core.py`
**Do:** Make `RealLog.__init__` accept optional `VaultSettings`. Delegate schema init to `RealLogDB` only (remove duplicate). Read JWT secret from `user_preferences` on init.
**Acceptance:** `RealLog(settings=VaultSettings())` works. No duplicate schema init. `pytest tests/` green.
**Depends on:** Tasks 2, 3.

### Task 5: Gateway server
**Files:** `gateway/__init__.py`, `gateway/server.py`, `gateway/auth.py`, `gateway/routes.py`, `gateway/deps.py`
**Do:**
- `server.py`: Starlette app. Middleware: CORS, slowapi rate limiting.
- `auth.py`: JWT issue/verify using secret from `RealLog`. `POST /auth/login` — accepts `{"passphrase":"..."}`, returns `{"token":"..."}`. For Phase 0, passphrase is `LOG_MCP_PASSPHRASE` env var (required).
- `routes.py`: `POST /v1/chat/completions` — accepts OpenAI-format body, dehydrates all `messages[].content`, prepends preamble to system message, forwards to configured cloud API, rehydrates response, returns. `GET /` — serves `web/index.html`. `GET /stats` — returns entity/session counts from vault.
- `deps.py`: Singleton `RealLog` instance factory.
**Acceptance:** `httpx.AsyncClient` TestClient can POST to `/v1/chat/completions` and get a response. Rate limit returns 429 after 30 requests.
**Depends on:** Tasks 3, 4.

### Task 6: Chat UI
**Files:** `web/index.html` (single file, inline CSS+JS)
**Do:** Single-page app. Dark theme, 720px max-width, mobile-friendly. Login screen → passphrase input → stores JWT in sessionStorage. Chat area with message bubbles. Input textarea, Enter to send. Status indicator ("Sending…", "Error"). HTTP POST to `/v1/chat/completions` (no WebSocket). Simple conversation history in-memory (lost on refresh — acceptable for Phase 0).
**Acceptance:** Open `localhost:8000` in browser → login → type message → see response.
**Depends on:** Task 5.

### Task 7: PII leakage + coherence tests
**Files:** `tests/test_gateway.py`, `tests/conftest.py`
**Do:**
- `conftest.py`: Shared fixture — tmp vault, Starlette TestClient with mock cloud API.
- `test_gateway.py`: (1) Send PII-laden message, capture what reaches mock upstream — assert zero real PII. (2) Rehydrated response contains original PII values. (3) **Coherence test**: mock cloud returns a plausible response to dehydrated text — assert it references entity tokens naturally. (4) Rate limit test — 31st request returns 429. (5) Auth test — unauthenticated request returns 401.
**Acceptance:** All tests pass. `pytest tests/test_gateway.py` green.
**Depends on:** Tasks 5, 6.

### Task 8: Docker setup
**Files:** `docker/Dockerfile`, `docker/docker-compose.yml`, `docker/.env.example`
**Do:**
- `Dockerfile`: Python 3.12-slim, install deps (`starlette`, `uvicorn`, `pydantic-settings`, `httpx`, `slowapi`, `python-jose`), copy `vault/` and `gateway/` and `web/`, CMD `uvicorn gateway.server:app --host 0.0.0.0 --port 8000`.
- `docker-compose.yml`: Single `log-mcp` service. Port 8000. Volume for vault data. No ollama, no cloudflared (those are later phases).
- `.env.example`: `LOG_API_KEY=`, `LOG_MCP_PASSPHRASE=`, `LOG_PROVIDER_ENDPOINT=https://api.openai.com/v1/chat/completions`.
**Acceptance:** `docker compose up` → `localhost:8000` serves chat UI → can login and send message.
**Depends on:** Task 7.

### Task 9: End-to-end smoke test
**Files:** `tests/test_e2e.py`
**Do:** Test that starts real Docker container (or subprocess), hits `localhost:8000`, logs in, sends message, verifies response. Marked with `@pytest.mark.e2e` so CI can skip it.
**Acceptance:** `pytest -m e2e` passes when Docker is running.
**Depends on:** Task 8.

---

## Revised Roadmap

| Phase | Scope | Trigger |
|---|---|---|
| **0** | MVP: Docker + chat UI + cloud proxy + PII (this plan) | Now |
| **1** | Tunnel (cloudflared) + phone access + vault encryption CLI | Users asking for remote access |
| **2** | Local LLM (Ollama) + routing classifier | Users asking for offline/no API key |
| **3** | Memory/context (FTS5 + embeddings) | Users complaining "it doesn't remember" |
| **4+** | TBD based on feedback | Real user demand, not speculation |

Phases 4–6 from ROADMAP-v2 are deleted. Training pipeline → "export JSONL" if anyone asks. Federated learning and model marketplace → pure fantasy.
