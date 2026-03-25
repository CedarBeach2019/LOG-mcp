# vault/

The data and persistence layer for LOG-mcp. Handles PII detection, dehydration/rehydration, local inference, semantic caching, routing intelligence, and all SQLite storage.

## Files

| File | Lines | Purpose |
|------|------:|---------|
| `core.py` | 836 | **RealLog** (SQLite storage), **Dehydrator** (PII → entity tokens), **Rehydrator** (tokens → original text), **DatabaseConnection** (context manager), PII patterns |
| `config.py` | 68 | **VaultSettings** — all config via `LOG_` env vars (Pydantic) |
| `routing_script.py` | 118 | **classify()** and **resolve_action()** — rule-based message classifier (~5ms, zero ML) |
| `profiles.py` | 191 | **ProfileManager** — CRUD for custom model profiles, default profiles, JSON persistence |
| `draft_profiles.py` | 5 | Legacy alias for default profiles (re-exports from profiles) |
| `local_inference.py` | 161 | **LocalInferenceBackend** — llama-cpp-python wrapper (lazy load, thread-safe, streaming, embeddings) |
| `model_manager.py` | 100 | **ModelManager** — scan `.gguf` files, auto-select by VRAM budget, hot-swap models |
| `semantic_cache.py` | 169 | **SemanticCache** — in-memory LRU with cosine similarity, TTL, negative feedback invalidation |
| `stats_collector.py` | 245 | **RoutingStats** — compute per-route, per-model, per-profile metrics from interactions |
| `routing_updater.py` | 269 | **RoutingUpdater** — suggest/approve/apply routing rule changes from stats, history tracking |
| `gpu_utils.py` | 66 | **get_gpu_memory_info()**, **calculate_optimal_gpu_layers()** — nvidia-smi + tegrastats |
| `reallog_db.py` | 148 | Database schema definitions and migration helpers |
| `archiver.py` | 334 | Data archival — export sessions to JSON, compress old data |
| `cli.py` | 281 | CLI interface for vault operations (import, export, stats) |
| `llm_scorer.py` | 112 | **LLMScorer** — score response quality for draft ranking |
| `__init__.py` | 1 | Package marker |

## Key Concepts

### PII Pipeline
1. **Detect** — Dehydrator uses compiled regex to find emails, phones, SSNs, credit cards, API keys
2. **Replace** — Entity tokens like `[EMAIL_A]`, `[PHONE_B]` maintain LLM coherence
3. **Send** — Only tokens reach the cloud API
4. **Restore** — Rehydrator swaps tokens back before showing to user

### Routing
- `classify(message, length, has_code)` → `{"action": "...", "confidence": 0.7}`
- Actions: `CHEAP_ONLY`, `ESCALATE`, `COMPARE`, `DRAFT`, `LOCAL`, `MANUAL_OVERRIDE`
- `resolve_action()` maps actions to endpoint type + model name
- Default: escalate when uncertain

### Semantic Cache
- LRU eviction (configurable max size)
- Cosine similarity matching via sentence-transformers (384 dims)
- TTL expiry, invalidation on 👎 feedback
- Falls back to exact match when no embedding function available

### Local Inference
- llama-cpp-python with optional CUDA offloading
- Auto GPU layer detection based on model size and available memory
- Embeddings via sentence-transformers (BERT GGUF not supported by llama.cpp)
- Graceful degradation — everything works without llama-cpp-python installed
