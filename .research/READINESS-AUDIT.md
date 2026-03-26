# LOG-mcp Production Readiness Audit

**Date:** 2026-03-25
**Auditor:** Automated
**Commit:** c976402
**Scope:** Full codebase at `/tmp/LOG-mcp/`

---

## Dimension Scores

### 1. Deployability — 6/10

**Good:** Quick start in README is accurate (`pip install -r requirements.txt && python -m gateway.server`). Docker setup exists with compose file. Jetson-specific `requirements-jetson.txt` mentioned. Env vars documented in README.

**Bad:**
- `requirements.txt` lists 6 packages. Missing: `sentence-transformers` (optional but needed for semantic cache/embeddings), `llama-cpp-python` (for local inference). These are heavy installs not documented as optional deps.
- Dockerfile `pip install sentence-transformers 2>/dev/null || true` will silently fail on constrained environments with no clear feedback.
- No `.env.example` file. Users must guess env var names from README.
- `scripts/vault-init.sh` exists but no mention in README quick start.
- `LOG_API_KEY` implies single key for single provider. Multi-provider setup not documented.

### 2. API Completeness — 7/10

**Good:** `/v1/chat/completions` works end-to-end with full pipeline (auth → cache → PII → routing → model call → rehydrate → store). Draft mode (`/v1/drafts`), feedback, sessions, config all wired. Streaming implemented.

**Bad:**
- OpenAI compatibility is partial: no `tools`/`function_calling` passthrough, no `logprobs`, no `top_p`, `top_k`, `stop` sequences in `call_model()` (`gateway/shared.py:47`). The `body` dict only sets `model`, `messages`, `temperature`, `stream`.
- No `model` field validation — passes whatever the client sends through to upstream.
- Compare mode returns two responses in parallel but doesn't expose them in OpenAI-compatible format.
- `/v1/chat/completions` returns non-standard fields (`route`, `cached`, `interaction_id`) alongside standard `choices` — will confuse strict OpenAI SDK clients.

### 3. Provider Wiring — 3/10

**Critical finding:** `ProviderRegistry` in `vault/providers/__init__.py` (lines 153-234) is **completely disconnected** from the actual request pipeline. It is never imported in `gateway/routes.py`, `gateway/shared.py`, or any gateway module. `call_model()` in `gateway/shared.py:47` takes raw `endpoint` and `api_key` strings and makes a single HTTP call — no registry lookup, no failover chains, no multi-provider selection.

The routing in `gateway/routes.py:~280` still uses hardcoded `settings.cheap_model_endpoint` and `settings.escalation_model_endpoint`. The BUILTIN_PROVIDERS dict (deepseek, groq, openai, openrouter, local) is defined but never registered or queried at runtime.

**This is scaffolding, not a feature.** The README claims "pluggable providers" but only DeepSeek actually works out of the box.

### 4. Error Handling — 6/10

**Good:** `error_boundary.py` exists. Retry + fallback pattern documented in architecture. `call_model()` returns `(status, data, error)` tuple — callers handle failures. `_call_draft_profile()` catches exceptions per-profile (line ~100). Graceful degradation for local model → cloud fallback.

**Bad:**
- No rate limit retry with `Retry-After` header handling visible in `call_model()`.
- `health()` endpoint checks API key by hitting `/models` and accepts 401/403 as "reachable" (`gateway/routes.py:~105`) — this masks a broken key as healthy.
- No timeout handling in streaming path — if upstream hangs, connection stays open indefinitely.
- `call_model()` catches generic `Exception` — specific error types (timeout, connection, auth) are not distinguished for adaptive routing.

### 5. Database Migrations — 2/10

**Critical finding:** `vault/core.py:128` uses `CREATE TABLE IF NOT EXISTS` in `executescript()` — this creates tables but never alters them. There is **no migration system**. If any table schema changes (adding columns, new tables), existing databases will silently fail with missing columns.

The tables: `sessions`, `messages`, `pii_map`, `user_preferences`, `interactions`. No version tracking. No schema migration tool (no Alembic, no manual migrations directory).

**Adding a new column to any table will break existing deployments.** This is a time bomb.

### 6. Security — 4/10

**Bad:**
- **JWT secret stored in SQLite** (`gateway/auth.py:15-28`) — fetched from `jwt_secret` key in DB, generated if missing. Not configurable via env var. On first run, secret is generated and persisted. If DB is compromised, tokens are forgeable.
- **Passphrase comparison is not timing-safe** (`gateway/routes.py:~68`): `passphrase != settings.passphrase` is vulnerable to timing attacks.
- **CORS default is `*`** (`gateway/server.py:161`) — allows any origin. README documents `LOG_CORS_ORIGINS=*` but doesn't warn about the security implications.
- **No CSRF protection** on state-changing endpoints.
- **API keys in URLs/env vars** — standard but `get_auth_headers()` in providers leaks first 8 chars of key in `to_dict()` (`vault/providers/__init__.py:~104`) — could appear in logs/UI.
- **No request body size limit** — large payloads could exhaust memory.
- SQL injection risk is low (parameterized queries used) but `vault/core.py:110` runs `PRAGMA` statements directly.

### 7. UI Completeness — 6/10

**Good:** Single-page app with login, chat, drafts, feedback, settings panel, profiles, session history modal. Dark theme. Route badges, draft cards with ranking, streaming with blinking cursor.

**Bad:**
- ~1700 lines of HTML/CSS/JS in one file — ROADMAP itself calls this "unmaintainable" (Phase 4.3).
- No keyboard shortcuts (ROADMAP lists as TODO).
- No mobile responsive design (ROADMAP lists as TODO).
- Session list is a modal, not persistent sidebar (ROADMAP lists as TODO).
- Metrics panel, adaptive routing dashboard, training export — unclear if UI buttons exist for these endpoints.
- No light mode toggle.

### 8. Documentation — 5/10

**Good:** README is well-written with quick start, feature table, config vars, deployment options. ARCHITECTURE.md has excellent diagram and file structure. ROADMAP-v4.md is detailed with success metrics.

**Bad:**
- ARCHITECTURE.md file structure lists files that may not exist (`vault/unified_store.py`, `vault/model_manager.py` referenced but actual filenames differ).
- No API reference with request/response schemas. Users must read source code.
- No contributing guide, no changelog.
- ROADMAP checkboxes are all unchecked (Phase 4) despite README claiming features like "semantic cache" and "adaptive routing" as if working.
- Version pinned to commit hash c976402 — no semver.

### 9. Testing Quality — 5/10

**Good:** 30+ test files, ~4800 lines of test code. Covers most modules. Test file for almost every feature.

**Bad:**
- 198 mock/patch calls across tests — heavy mocking means tests verify mocks, not real behavior.
- `demo_e2e.py` exists but unclear if it's actually an integration test or a demo script.
- No test for the full request pipeline (auth → cache → PII → route → model call → rehydrate → response) against a real or test database with seeded data.
- `conftest.py` exists but fixture reuse across files unknown.
- No CI configuration visible (no `.github/workflows/`, no `Makefile`, no `tox.ini`).
- Badge says "325 passing" but no way to verify.

### 10. Missing Critical Features — 5/10

- **No database migration system** — schema changes will break deployments
- **Provider registry not wired** — multi-provider is documentation only
- **No streaming error recovery** — hung connections not handled
- **No OpenAI tools/function_calling passthrough** — limits usefulness
- **No `usage` field on streaming responses** — OpenAI-compatible clients expect this
- **No webhook/event system** — no way to integrate with external tools
- **No backup/restore for SQLite** — data loss risk
- **No log rotation** — SQLite grows unbounded
- **No multi-user support** — single passphrase, no RBAC
- **Rate limiter (`slowapi`) in requirements.txt but not visible in middleware** — either not wired or wired elsewhere

---

## Issue Categories

### BLOCKERS (must fix before any release)

1. **No database migration system** — `vault/core.py:128`. Any schema change breaks existing databases with zero recovery path. Add Alembic or manual versioned migrations before changing any table.

2. **Provider registry is dead code** — `vault/providers/__init__.py` is never imported by gateway. README and ARCHITECTURE claim multi-provider support. Either wire it or remove the claims.

3. **JWT secret not configurable** — `gateway/auth.py:15-28`. Secret generated and stored in DB only. Must be settable via env var (`LOG_JWT_SECRET`) with a warning if not set.

4. **Timing-unsafe passphrase comparison** — `gateway/routes.py:68`. Use `hmac.compare_digest()`.

### IMPORTANT (should fix before public announcement)

5. **No `stop`, `max_tokens`, `top_p` passthrough** in `call_model()` (`gateway/shared.py:47`). OpenAI compatibility is broken for common use cases.

6. **Streaming has no timeout** — upstream hang = connection hang. Add `asyncio.wait_for` or httpx timeout on streaming responses.

7. **CORS default `*`** — `gateway/server.py:161`. Default should be `localhost:8000` or empty (reject all), not wildcard.

8. **No request body size limit** — DOS vector. Add middleware or Starlette config.

9. **API key health check is wrong** — `gateway/routes.py:105` treats 401/403 as "reachable/healthy". Should treat non-200 as unhealthy.

10. **No CI pipeline** — no `.github/workflows/`. "325 passing tests" badge is unverifiable without CI.

11. **Rate limiter in `requirements.txt` but not in middleware** — `slowapi` listed but `gateway/server.py` doesn't use it. ROADMAP lists rate limiting as TODO despite README claiming it.

12. **API keys leaked in provider `to_dict()`** — `vault/providers/__init__.py:104`. First 8 chars exposed.

### POLISH (can ship without, should fix soon)

13. **Single 1700-line HTML file** — split into JS modules per ROADMAP Phase 4.3.
14. **No mobile responsive design** — ROADMAP TODO.
15. **No `.env.example`** — reduce setup friction.
16. **No backup/restore mechanism** for SQLite.
17. **No log rotation / DB size management**.
18. **No contributing guide or changelog**.
19. **Non-standard response fields** (`route`, `cached`, `interaction_id`) on `/v1/chat/completions` — document or remove for strict compatibility.
20. **No keyboard shortcuts** — ROADMAP TODO.
21. **ARCHITECTURE.md file listing is inaccurate** — references files that may not exist at listed paths.

---

## Summary

LOG-mcp has **impressive architectural vision** and a **solid core pipeline** (PII dehydration, routing, draft comparison, feedback loop). The README sells a complete product. But the gap between README promises and working code is significant.

**The two biggest risks are:**
1. **Provider registry is scaffolding** — multi-provider, the headline Phase 6 feature, doesn't exist at runtime.
2. **No migration system** — every schema change is a breaking change for existing users.

**Verdict: Not ready for public release.** The core chat/draft/feedback loop works for single-provider (DeepSeek), but multi-provider claims are false, and there's no path to safe schema evolution. Fix the blockers above and this becomes a strong v0.1 alpha release.
