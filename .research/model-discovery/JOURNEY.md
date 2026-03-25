# Model Discovery & Auto-Benchmarking — Research Journey

## Phase 1: Codebase Analysis

### Existing Components

**vault/model_lifecycle.py**: Static `MODEL_CATALOG` dict of local GGUF models. Functions for VRAM estimation, download from HuggingFace, hot-swap. Hardcoded catalog of ~5 models (qwen, phi, llama, bge embeddings).

**vault/model_manager.py**: `ModelManager` class — scans `.gguf` files in a directory, load/unload/auto-select. Thread-safe with lock. Supports subprocess mode for Jetson.

**vault/adaptive_routing.py**: `ModelHealth` dataclass tracks requests, latency, satisfaction. `CostTracker` has hardcoded pricing for ~6 models. `AdaptiveRouter` combines health + cost + calibration. Already has `reliability_score` (0-1).

**vault/config.py**: `VaultSettings` with `LOG_` env prefix. Has `cheap_model_name`, `escalation_model_name`, `provider_endpoint`. No OpenRouter config yet.

**gateway/routes.py**: ~700 lines, all endpoints as async functions. Routes registered in `server.py`.

### Key Findings
- Model catalog is **static and local-only** — no API provider integration
- Pricing is hardcoded in CostTracker — no dynamic pricing
- No benchmarking infrastructure exists
- Adaptive routing tracks runtime metrics but can't compare unknown models

## External Research

### OpenRouter API
- **GET https://openrouter.ai/api/v1/models** returns full model list
- Returns: id, name, context_length, pricing (prompt/completion per token), architecture, top_provider, pricing details
- Free models available. Supports Anthropic, OpenAI, Google, Meta, etc.
- API key for actual calls; model list is publicly accessible

### Standard LLM Benchmarks
- **MMLU**: Multiple-choice knowledge, 57 subjects. Heavy for local eval.
- **HumanEval**: Code generation (164 problems). Evaluates correctness via unit tests.
- **GSM8K**: Grade school math. Tests reasoning.
- For a personal gateway: latency, cost, and task-specific quality matter more than aggregate benchmarks.

### Benchmarking on Constrained Hardware (Jetson 8GB)
- Can't run full MMLU/HumanEval locally
- Strategy: **API-based benchmarks** (call the model, score the response)
- Use **tiny subsets** (5-10 prompts) for quality checks
- Latency benchmarks: time-to-first-token, total time (API calls, not local inference)
- SQLite for storing results — already used throughout the project
- **Keep it async** to not block the gateway

### Metrics That Matter
1. **Latency**: TTFT, total generation time (ms)
2. **Quality**: Response correctness on standardized prompts (rubric-based)
3. **Cost**: $ per 1M tokens from API pricing + actual token usage
4. **Reliability**: Error rate, timeout rate

## Proposed Architecture

### vault/model_discovery.py
- `ModelDiscovery` class with cached OpenRouter model list
- Fetch from API or use local cache file
- Filter/sort by: capability, context length, pricing, provider
- Search by name/keyword

### vault/benchmark.py
- `BenchmarkRunner` class
- Latency benchmark: call API, measure TTFT + total time
- Quality benchmark: small prompt sets per category (code, chat, reasoning)
- Score with rubric (keyword match, format compliance, code syntax)
- Store in SQLite table `benchmark_results`

### vault/model_comparator.py
- `ModelComparator` class
- Takes benchmark results + pricing
- Weighted scoring for use cases (code, chat, reasoning, general)
- `pick_best(task_type, constraints)` → ranked model list

### API Endpoints
- GET /v1/discovery/search?q=...&capability=...&max_price=...
- GET /v1/discovery/benchmark?model=...&run=true
- POST /v1/discovery/compare {"models": [...], "task": "code"}
