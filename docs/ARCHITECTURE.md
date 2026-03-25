# ARCHITECTURE.md — System Architecture

## Overview

LOG-mcp is a personal AI gateway that sits between users and AI services. It provides
intelligent routing, privacy protection, draft comparison, preference learning, and
optional local inference.

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Layer                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │
│  │  Web UI  │  │  cURL/   │  │  OpenAI  │  │  Custom App  │    │
│  │ index.htm│  │  scripts │  │  SDK     │  │  integration │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘    │
│       └──────────────┴────────────┴───────────────┘             │
│                          │ POST /v1/chat/completions             │
└──────────────────────────┼──────────────────────────────────────┘
                           │
┌──────────────────────────┼──────────────────────────────────────┐
│                     Gateway Layer (Starlette)                    │
│                          │                                      │
│  ┌───────────┐  ┌───────▼───────┐  ┌───────────────┐          │
│  │   Auth    │  │   PII Engine  │  │   Cache       │          │
│  │   JWT     │→ │  Dehydrator   │  │  SemanticCache│          │
│  │           │  │  Rehydrator   │  │  (LRU+sim)   │          │
│  └───────────┘  └───────┬───────┘  └───────────────┘          │
│                        │                                       │
│  ┌─────────────────────▼──────────────────────────┐            │
│  │              Routing Script                     │            │
│  │  classify(message) → CHEAP_ONLY | ESCALATE |    │            │
│  │  DRAFT | MANUAL_OVERRIDE | LOCAL               │            │
│  │  ~5ms, regex-based, zero ML                    │            │
│  └─────────────────────┬──────────────────────────┘            │
│                        │                                       │
│  ┌─────────┬───────────┼───────────┬──────────┐               │
│  │  Local  │   Cheap   │  Escalate  │  Draft   │               │
│  │ llama.  │  deepseek │  deepseek  │ parallel │               │
│  │ cpp     │  -chat    │  -reasoner │  profiles│               │
│  │ (GPU)   │  (cloud)  │  (cloud)   │  (cloud) │               │
│  └────┬────┘  └────┬────┘  └────┬─────┘  └────┬────┘         │
│       └────────────┴────────────┴────────────┘                │
│                          │                                      │
│  ┌───────────────────────▼──────────────────────┐              │
│  │              Feedback & Preferences          │              │
│  │  👍👎 → interactions table → StatsCollector   │              │
│  │  → RoutingUpdater → updated routing rules     │              │
│  └──────────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────┼──────────────────────────────────────┐
│                     Data Layer (SQLite)                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐        │
│  │ interactions │  │  pii_map     │  │ user_        │        │
│  │ (chat log +  │  │ (entity      │  │ preferences  │        │
│  │  feedback +  │  │  tokens)     │  │ (learned     │        │
│  │  routing)    │  │              │  │  defaults)   │        │
│  └──────────────┘  └──────────────┘  └──────────────┘        │
│  ┌──────────────┐  ┌──────────────┐                           │
│  │ routing_     │  │ profiles.json│                           │
│  │ updates      │  │ (custom      │                           │
│  │ (history)    │  │  providers)  │                           │
│  └──────────────┘  └──────────────┘                           │
└─────────────────────────────────────────────────────────────────┘
```

## Key Design Decisions

### Why Rule-Based Routing (Not ML)
- Runs in ~5ms via regex (no model inference needed)
- Interpretable and debuggable
- ML optimizes the rules over time, never blocks requests
- Default to escalate when uncertain (safer than under-serving)

### Why Instant-Send Architecture
- Cheap model fires immediately on connection (~0ms added latency)
- Routing classification runs in parallel with the cheap model call
- If escalation needed, fires escalation while cheap model response streams back
- User sees first response faster, gets escalation quality if needed

### Why SQLite (Not PostgreSQL)
- Zero-config, no external service dependency
- Perfect for single-user self-hosted deployment
- WAL mode enables concurrent reads
- Jetson-friendly (no container overhead)

### Why In-Process llama.cpp (Not Ollama)
- Zero IPC overhead (ctypes bindings)
- Full control over GPU memory allocation
- Native prompt caching support
- LoRA hot-swapping
- Works with async Python via `asyncio.to_thread`

### Why Semantic Cache (Not Just Exact Match)
- "What is machine learning?" and "How does ML work?" should share cache
- Cosine similarity on sentence-transformer embeddings (384 dims)
- Falls back to exact match when no embedding function available
- Invalidated on negative feedback

## Module Map

```
vault/
  config.py           — Pydantic settings (LOG_ env prefix)
  core.py             — RealLog (SQLite), Dehydrator, Rehydrator, PII detection
  routing_script.py   — Rule-based message classifier
  profiles.py         — ProfileManager (JSON persistence, CRUD)
  draft_profiles.py   — Re-exports from profiles (backwards compat)
  local_inference.py  — LocalInferenceBackend (llama-cpp-python)
  model_manager.py    — ModelManager (scan, load, auto-select)
  semantic_cache.py   — SemanticCache (LRU, cosine sim, TTL)
  gpu_utils.py        — GPU memory detection, auto layer calculation
  stats_collector.py  — RoutingStats computation from interactions
  routing_updater.py  — Routing rule suggestions from stats
  reallog_db.py       — Database schema and migrations

gateway/
  routes.py           — Core routing, _call_model, shared utilities
  api_auth.py         — Login handler
  api_chat.py         — Chat completions, drafts, elaborate
  api_feedback.py     — Feedback CRUD
  api_preferences.py  — User preferences CRUD
  api_profiles.py     — Custom profiles CRUD
  api_routing.py      — Routing stats, suggestions, updates
  api_local.py        — Local model management
  api_cache.py        — Cache stats and clear
  api_system.py       — Health, stats, static files
  server.py           — Starlette app, route registration
  auth.py             — JWT creation and verification
  deps.py             — Singleton managers (settings, reallog)

web/
  index.html          — Dark-theme SPA (1192 lines, inline CSS/JS)

tests/                 — 187 tests
docs/                  — Architecture docs, roadmap, research
```

## API Surface

### Core
- `POST /v1/chat/completions` — OpenAI-compatible chat (with routing, cache, PII)
- `POST /auth/login` — Get JWT token

### Feedback & Preferences
- `POST /v1/feedback` — Submit 👍👎 with optional critique
- `GET/POST/DELETE /v1/preferences` — View/set/delete learned preferences

### Profiles
- `GET /v1/profiles` — List all profiles
- `POST /v1/profiles` — Create custom profile
- `DELETE /v1/profiles/{name}` — Delete custom profile

### Draft Round
- `POST /v1/drafts` — Get parallel short responses from multiple profiles
- `POST /v1/elaborate` — Winner expands into full response

### Local Inference
- `GET /v1/local/models` — List available .gguf files
- `POST /v1/local/load` — Load a model (auto-detects GPU layers)
- `POST /v1/local/unload` — Unload current model
- `GET /v1/local/status` — Loaded model info

### Cache
- `GET /v1/cache/stats` — Hit rate, size, entries
- `POST /v1/cache/clear` — Clear cache

### Routing Intelligence
- `GET /v1/stats/routing` — Per-class, per-model, per-profile stats
- `POST /v1/routing/suggest` — Dry-run routing rule updates
- `POST /v1/routing/update` — Apply routing updates
- `GET /v1/routing/history` — Past routing changes

### System
- `GET /` — Chat UI
- `GET /v1/health` — Service health check
- `GET /stats` — Overall statistics
