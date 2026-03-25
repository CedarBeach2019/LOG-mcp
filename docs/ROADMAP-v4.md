# LOG-mcp Roadmap v4 — From Prototype to Platform

_Updated 2026-03-25. 65 commits, 250 tests, all architectural gaps closed._

## Where We Are

The foundation is solid. We have:
- **PII dehydration/rehydration** — privacy-first by default
- **Intelligent routing** — static + dynamic rules, auto-optimizing from feedback
- **Draft round** — multi-model comparison with user ranking (the moat)
- **Error boundaries** — retry + fallback + friendly errors, never a raw 502
- **Unified storage** — single source of truth in SQLite
- **Observability** — tracing middleware, metrics dashboard, per-request timing
- **Local inference** — subprocess-isolated GPU on Jetson, sentence-transformer embeddings
- **Semantic cache** — cosine similarity matching, embeddings wired up
- **Training pipeline** — LoRA/DPO export from draft rankings
- **Session management** — persistent conversations, history, auto-titles
- **Streaming** — SSE with blinking cursor
- **Auth** — JWT with passphrase

## Phase 4: Hardening & Real Usage (This Week)

The system works. Now make it *reliable* for daily use.

### 4.1: Production Readiness
- [ ] **Health check depth** — verify DB, model, API keys, disk space in `/v1/health`
- [ ] **Graceful shutdown** — SIGTERM handler saves state, stops subprocess, closes DB
- [ ] **Startup validation** — fail fast if config is broken (bad DB path, missing dirs)
- [ ] **Rate limiting** — per-IP token bucket on `/v1/chat/completions` (prevent abuse)
- [ ] **CORS tightening** — configurable origins instead of wildcard
- [ ] **Structured error responses** — consistent `{error, code, detail}` format

### 4.2: Config That Actually Works
- [ ] **Runtime config reload** — PUT `/v1/config` updates settings without restart
- [ ] **Config validation** — reject invalid API keys, bad model names, impossible timeouts
- [ ] **Environment-driven defaults** — LOG_CHEAP_MODEL, LOG_ESCALATION_MODEL, etc.
- [ ] **Profile-switching endpoint** — change active model profile via API

### 4.3: UI v2 (Component Architecture)
The 1500-line HTML file works but is unmaintainable. Split into:
- [ ] **JavaScript modules** — `api.js`, `chat.js`, `drafts.js`, `settings.js`, `cache.js`
- [ ] **CSS custom properties** — theme tokens for light/dark mode toggle
- [ ] **Session sidebar** — persistent list of conversations (not a modal)
- [ ] **Metrics panel** — embed `/v1/metrics` data in settings
- [ ] **Keyboard shortcuts** — Ctrl+Enter send, Ctrl+N new chat, /commands autocomplete
- [ ] **Mobile responsive** — touch-friendly draft cards, sidebar drawer

## Phase 5: The Intelligence Layer (Week 2-3)

This is where LOG-mcp becomes *more than a proxy*.

### 5.1: Adaptive Routing v2
- [ ] **Per-user learning** — routing rules scoped to session patterns
- [ ] **Confidence calibration** — compare predicted vs actual feedback, adjust thresholds
- [ ] **Cost optimization** — track $/request, auto-swap cheaper models when accuracy is high
- [ ] **Model health scoring** — track per-model latency, error rate, satisfaction; route around degraded providers

### 5.2: Local Model Lifecycle
- [ ] **Auto-download** — fetch GGUF models from HuggingFace by name
- [ ] **Quantization selection** — pick Q4_K_M vs Q5_K_M based on available VRAM
- [ ] **Hot-swap without downtime** — load new model, switch traffic, unload old
- [ ] **LoRA fine-tuning execution** — actually run the training from exported data
- [ ] **A/B testing** — serve LoRA-finetuned model to 10% of requests, measure satisfaction

### 5.3: Prompt Intelligence
- [ ] **System prompt templates** — per-profile system prompts with variable substitution
- [ ] **Prompt compression** — for cache keys: strip PII tokens, normalize whitespace
- [ ] **Few-shot injection** — auto-include relevant past interactions as examples
- [ ] **Context window management** — truncate/prioritize messages to fit model context

## Phase 6: Multi-Provider (Week 3-4)

Break free from single-provider lock-in.

### 6.1: Provider Abstraction
- [ ] **Provider registry** — pluggable providers (DeepSeek, Groq, OpenAI, local, future)
- [ ] **Unified API format** — normalize OpenAI-compatible responses from any provider
- [ ] **Failover chains** — provider A → provider B → local model → error
- [ ] **Load balancing** — round-robin across equivalent providers
- [ ] **Cost tracking per provider** — track spend, set budgets

### 6.2: Model Discovery
- [ ] **OpenRouter integration** — access 100+ models through one API
- [ ] **Auto-benchmarking** — send test prompts, measure latency/quality/cost
- [ ] **Smart provider selection** — choose provider based on prompt type, budget, latency

## Phase 7: The Moat (Month 2)

The draft round dataset. This is what doesn't exist anywhere else.

### 7.1: Dataset Quality
- [ ] **Deduplication pipeline** — canonicalize prompts, merge similar rankings
- [ ] **Quality scoring** — flag low-effort rankings (instant clicks, no reasoning)
- [ ] **Diversity sampling** — ensure coverage across domains (code, writing, math, creative)
- [ ] **Annotation interface** — web UI for reviewing/correcting rankings

### 7.2: Training Execution
- [ ] **LoRA training runner** — using llama-cpp-python or axolotl
- [ ] **Evaluation harness** — before/after benchmark on held-out rankings
- [ ] **Model registry** — track trained models, their training data, eval scores
- [ ] **Automatic deployment** — promote model when eval score > baseline

### 7.3: The Flywheel
- [ ] **Anonymous telemetry opt-in** — share ranking distributions (no content)
- [ ] **Cross-user aggregation** — "87% of users prefer model X for code questions"
- [ ] **Model recommendation engine** — suggest models based on usage patterns
- [ ] **Open dataset release** — publish anonymized comparative rankings

## Non-Goals (Still)

- Mobile native app (web is sufficient)
- Plugin/extension ecosystem (complexity vs value)
- Multi-tenant SaaS (single-user for now)
- Voice input/output (TTS/STT integration is separate)

## Success Metrics

| Metric | Current | Target (Phase 4) | Target (Phase 7) |
|--------|---------|------------------|------------------|
| Tests passing | 250 | 300+ | 500+ |
| Cache hit rate | ~0% (not wired) | 15%+ | 40%+ |
| Routing accuracy | ~70% (static) | 80%+ (dynamic) | 90%+ (learned) |
| Error recovery | 0% (raw 502) | 95%+ (retry+fallback) | 99%+ |
| Local inference | CPU only | GPU subprocess | LoRA fine-tuned |
| Training data | 0 rankings | 50+ rankings | 1000+ rankings |
| Avg latency | ~2s | <1.5s | <1s with cache |
