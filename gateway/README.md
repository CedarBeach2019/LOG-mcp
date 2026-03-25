# gateway/

The API and request-handling layer. Built on Starlette (async), serves the chat UI and all REST endpoints.

## Files

| File | Lines | Purpose |
|------|------:|---------|
| `server.py` | 85 | Starlette app, CORS middleware, static file mounting, all route registration |
| `routes.py` | 1090 | All 30+ route handlers: chat, drafts, feedback, preferences, profiles, routing, local, cache, sessions |
| `shared.py` | 109 | Shared utilities: `get_client()` (pooled httpx), `authenticate()`, `call_model()`, `get_local_manager()` |
| `auth.py` | 42 | JWT creation (`create_token`) and verification (`verify_token`) using HS256 |
| `deps.py` | 47 | Singleton factories: `get_settings()`, `get_reallog()`, `reset_all()` for test isolation |

## How Requests Flow

```
Browser/API → server.py → authenticate() → route handler → call_model() → cloud/local → rehydrate → JSON/SSE
```

1. **Auth**: Every protected endpoint calls `authenticate()` to validate JWT
2. **PII**: Chat handler dehydrates all user/system messages before forwarding
3. **Route**: `classify()` determines which model to use (cheap/escalate/draft/local/compare)
4. **Cache**: Semantic cache checked before model call; stored after on non-cached hits
5. **Model call**: `call_model()` uses shared httpx client with connection pooling
6. **Stream**: If `stream: true`, returns SSE; otherwise returns JSON
7. **Rehydrate**: Entity tokens replaced with original PII
8. **Store**: Interaction + messages saved to SQLite for history and analytics

## Route Handlers (routes.py)

### Core
- `login()` — `POST /auth/login` — passphrase → JWT
- `serve_index()` — `GET /` — chat UI
- `health()` — `GET /v1/health` — public, no auth
- `chat_completions()` — `POST /v1/chat/completions` — main chat (streaming, routing, cache, PII)

### Draft Comparison
- `drafts()` — `POST /v1/drafts` — 3 parallel profile calls
- `elaborate()` — `POST /v1/elaborate` — winner expands to full response

### Feedback & Preferences
- `feedback()` — `POST /v1/feedback` — 👍👎 with optional critique
- `preferences_list/set/delete()` — `GET/POST/DELETE /v1/preferences`

### Profiles
- `profiles_list/create/delete()` — `GET/POST/DELETE /v1/profiles`

### Routing Intelligence
- `routing_stats()` — `GET /v1/stats/routing`
- `routing_suggest()` — `POST /v1/routing/suggest` — dry-run rule changes
- `routing_update()` — `POST /v1/routing/update` — apply changes
- `routing_history()` — `GET /v1/routing/history`

### Local Inference
- `local_models_list()` — `GET /v1/local/models`
- `local_model_load()` — `POST /v1/local/load` — auto-detects GPU layers
- `local_model_unload()` — `POST /v1/local/unload`
- `local_model_status()` — `GET /v1/local/status`

### Cache
- `cache_stats()` — `GET /v1/cache/stats`
- `cache_clear()` — `POST /v1/cache/clear`

### Sessions
- `sessions_list()` — `GET /v1/sessions`
- `session_get()` — `GET /v1/sessions/{id}`
- `session_create()` — `POST /v1/sessions`
- `session_delete()` — `DELETE /v1/sessions/{id}`

## Testing

Routes are tested via `starlette.testclient.TestClient` with mocked `call_model()`. Each test gets an isolated SQLite DB via `tmp_path`. See `tests/test_gateway.py`, `tests/test_phase2.py`, `tests/test_drafts.py`, `tests/test_sessions.py`.
