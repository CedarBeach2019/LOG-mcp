# ARCHITECTURE.md — System Architecture

## Overview

LOG-mcp is a self-hosted AI gateway that sits between users and AI services. It provides
intelligent routing, privacy protection, draft comparison, preference learning, adaptive
model selection, and optional local inference on constrained hardware (Jetson).

```
┌─────────────────────────────────────────────────────────────────┐
│                        Client Layer                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐    │
│  │  Web UI  │  │  cURL/   │  │  OpenAI  │  │  Custom App  │    │
│  │ index.htm│  │  scripts │  │  SDK     │  │  integration │    │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └──────┬───────┘    │
│       └──────────────┴────────────┴───────────────┘             │
│               POST /v1/chat/completions (OpenAI-compatible)     │
└──────────────────────────┼──────────────────────────────────────┘
                           │
┌──────────────────────────┼──────────────────────────────────────┐
│                  Gateway Layer (Starlette)                       │
│                                                                 │
│  ┌──────────┐  ┌──────────────┐  ┌──────────┐  ┌───────────┐  │
│  │  Auth    │  │  Rate Limit  │  │ Tracing  │  │  CORS     │  │
│  │  JWT     │  │  Token Bucket│  │ Middleware│  │  Config   │  │
│  └──────────┘  └──────────────┘  └──────────┘  └───────────┘  │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │              Request Pipeline                            │   │
│  │  1. Auth check                                          │   │
│  │  2. Semantic cache lookup (if enabled + model loaded)   │   │
│  │  3. PII dehydration (privacy mode)                      │   │
│  │  4. Routing classification (static + dynamic optimizer)  │   │
│  │  5. Model call (with retry + fallback)                  │   │
│  │  6. PII rehydration                                     │   │
│  │  7. Session storage                                     │   │
│  │  8. Cache store + adaptive routing record               │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
│  ┌──────────────────────┐  ┌────────────────────────────────┐  │
│  │   Routing Script     │  │   Adaptive Router              │  │
│  │   Static + Dynamic   │  │   Model health + cost + calib  │  │
│  └──────────────────────┘  └────────────────────────────────┘  │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────┼──────────────────────────────────────┐
│                    Provider Layer                               │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Cheap Model  │  │ Escalation   │  │ Local Model            │  │
│  │ DeepSeek-    │  │ DeepSeek-    │  │ Subprocess (Jetson)   │  │
│  │ Chat         │  │ Reasoner     │  │ or in-process          │  │
│  └──────────────┘  └──────────────┘  └───────────────────────┘  │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Error Boundary: retry → fallback → friendly error        │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────┼──────────────────────────────────────┐
│                     Data Layer                                  │
│                                                                 │
│  ┌──────────────────────┐  ┌────────────────────────────────┐  │
│  │  SQLite (WAL mode)   │  │  vault/ (Python modules)       │  │
│  │  - interactions      │  │  - core.py (PII engine)        │  │
│  │  - routing_rules     │  │  - routing_script.py          │  │
│  │  - routing_opts      │  │  - routing_optimizer.py       │  │
│  │  - preferences       │  │  - adaptive_routing.py        │  │
│  │  - profiles          │  │  - semantic_cache.py          │  │
│  └──────────────────────┘  │  - local_inference.py         │  │
│                             │  - model_lifecycle.py        │  │
│                             │  - training_pipeline.py      │  │
│                             │  - prompt_intelligence.py    │  │
│                             └────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## File Structure

```
LOG-mcp/
├── gateway/                    # HTTP server (Starlette)
│   ├── server.py              # App setup, middleware, routes
│   ├── routes.py              # All API endpoints
│   ├── shared.py              # Shared utilities (auth, HTTP client, model manager)
│   ├── deps.py                # Settings singleton, DB reset
│   ├── error_boundary.py      # Retry + fallback + friendly errors
│   ├── tracing.py             # Request tracing middleware
│   ├── observability.py       # Metrics collector
│   ├── rate_limit.py          # Token bucket rate limiter
│   └── startup.py             # Startup validation
├── vault/                      # Business logic
│   ├── core.py                # PII dehydration/rehydration, RealLog DB
│   ├── config.py              # Settings dataclass (env-driven)
│   ├── routing_script.py      # Static + dynamic routing rules
│   ├── routing_optimizer.py   # DB-backed auto-optimizing rules
│   ├── adaptive_routing.py    # Model health, cost tracking, calibration
│   ├── semantic_cache.py      # LRU + cosine similarity cache
│   ├── local_inference.py     # In-process llama-cpp-python backend
│   ├── model_manager.py       # Model loading/unloading lifecycle
│   ├── model_subprocess.py    # Isolated GPU process for Jetson
│   ├── model_client.py        # Subprocess client (compatible API)
│   ├── model_lifecycle.py     # HuggingFace download, VRAM estimation, hot-swap
│   ├── training_pipeline.py   # LoRA/DPO export from draft rankings
│   ├── prompt_intelligence.py # System templates, context window, few-shot
│   └── unified_store.py       # Message storage migration
├── web/
│   └── index.html             # Single-file SPA (dark theme, ~1600 lines)
├── tests/                     # 325 tests
├── docs/                      # Documentation
├── scripts/                   # Setup and utility scripts
└── docker/                    # Docker deployment
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/login` | JWT authentication |
| POST | `/v1/chat/completions` | Main chat (OpenAI-compatible) |
| POST | `/v1/drafts` | Multi-model draft comparison |
| POST | `/v1/elaborate` | Expand winning draft |
| POST | `/v1/feedback` | Thumbs up/down + critique |
| GET | `/v1/health` | Deep health check (DB, model, disk, memory) |
| GET/POST/DELETE | `/v1/preferences` | User preferences |
| GET/POST/DELETE | `/v1/profiles` | Provider profiles |
| GET/POST/DELETE | `/v1/sessions` | Conversation sessions |
| GET/POST/DELETE | `/v1/cache` | Semantic cache |
| GET/POST | `/v1/local/*` | Local model management |
| GET | `/v1/local/catalog` | Available models to download |
| POST | `/v1/local/download` | Download model from HuggingFace |
| GET/POST | `/v1/routing/*` | Routing rules & optimization |
| GET | `/v1/metrics` | Request metrics dashboard |
| GET/PUT/POST | `/v1/config` | Runtime configuration |
| GET | `/v1/adaptive/*` | Adaptive routing dashboard |
| GET/POST | `/v1/training/*` | Training data export |

## Key Design Decisions

1. **SQLite WAL mode** — concurrent reads without blocking, single-file simplicity
2. **Subprocess model isolation** — GPU memory doesn't conflict with uvicorn on Jetson
3. **Rule-based routing, ML optimization** — regex runs in ~5ms; ML updates rules over time
4. **Draft round as core primitive** — multi-model comparison generates unique training data
5. **Error boundary pattern** — retry → fallback → friendly error (never raw 502)
6. **Feedback-driven learning** — every thumbs up/down feeds routing optimizer + calibration
7. **Singleton pattern for shared state** — settings, model manager, cache, router
