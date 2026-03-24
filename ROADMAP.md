# LOG-mcp Roadmap

**Latent Orchestration Gateway** — privacy-first middleware for AI agent ecosystems.

## Overview

LOG-mcp dehydrates PII from messages before they reach cloud APIs, then rehydrates responses for the user. It runs locally on edge hardware (Jetson) and aims to become the first-line contact for a person's entire agent ecosystem — always-on, self-improving, privacy-preserving.

---

## Phase 0 — MVP (Current)

**Status:** Shipping

### Goals
- Regex-based PII detection and redaction (names, emails, phones, addresses, SSNs, DOBs)
- SQLite-backed message store and audit log
- MCP server exposing redaction/rehydration tools
- CLI for manual testing and introspection

### Technical Approach
- Python with regex patterns compiled from NIST/NLP PII taxonomies
- Replace PII with typed placeholders: `{{PERSON_0}}`, `{{EMAIL_1}}`
- Rehydration map stored per-session in SQLite
- MCP server via `mcp` Python SDK, stdio transport
- CLI via `click` or `argparse`

### Dependencies
- Python 3.10+, `mcp` SDK, `sqlite3`
- No GPU required

### Success Metrics
- Detects standard PII categories at >95% recall on benchmark set
- Sub-10ms latency per message on regex pass
- MCP server responds within 50ms

### Timeline
✅ Complete

---

## Phase 1 — Local LLM Redaction

### Goals
- Replace regex PII detection with on-device inference (Jetson GPU)
- Handle contextual PII regex misses (nicknames, implicit references, relationships)
- Maintain sub-200ms redaction latency

### Technical Approach
- Run a small fine-tuned model via `llama.cpp` or `tensorrt-llm` on Jetson Orin GPU
- Base model: Phi-3-mini or Gemma-2-2b, fine-tuned on PII detection datasets (Presidio, i2b2, WNUT17)
- Input: raw message → output: structured redaction map (entity type, span, replacement token)
- Keep regex as fast-path fallback for obvious patterns; LLM handles ambiguous/implicit cases
- Model serves via local HTTP endpoint (e.g., `llama-cpp-python` server)
- Benchmark against Phase 0 regex on held-out test set before cut-over

### Dependencies
- Jetson with Orin GPU (8GB+ VRAM)
- `llama.cpp` with CUDA/arm64 support or TensorRT-LLM
- Training compute (can be cloud; only inference is local)
- PII fine-tuning dataset (curated + synthetic)

### Success Metrics
- F1 >0.95 on contextual PII detection (vs ~0.80 for regex alone)
- P99 latency <200ms for single-message redaction on Jetson
- Zero PII leaves the device unredacted

### Timeline
4–6 weeks

---

## Phase 2 — Intelligent Router

### Goals
- Route requests to the best AI provider/model per task (GPT-4o, Claude, Gemini, local model)
- Decision based on cost, speed, capability, current rate limits, token budget

### Technical Approach
- Local classifier (lightweight, runs on Jetson) categorizes incoming requests by task type: coding, reasoning, creative, summarization, retrieval, simple Q&A
- Router policy engine maintains per-provider profiles:
  - Cost per 1K tokens (input/output)
  - Average latency
  - Capability scores per task type (from benchmarking)
  - Current rate limit status
- Request scored against profiles → ranked provider list → first available wins
- Configurable policies: `cost-first`, `speed-first`, `quality-first`, `balanced`
- Fallback chain when primary provider is rate-limited or down

### Dependencies
- Phase 0 complete
- Provider API keys and billing access
- Task classification training data (can bootstrap from labeled request logs)

### Success Metrics
- Cost reduction >30% vs. single-provider routing on equivalent workload
- P95 routing decision latency <20ms
- Zero dropped requests (fallback always available)

### Timeline
3–4 weeks

---

## Phase 3 — Memory & Context Manager

### Goals
- Smart retrieval of conversation history relevant to current request
- Optimize context window usage — include what matters, drop what doesn't
- Token-efficient summarization of long conversations

### Technical Approach
- SQLite FTS5 for keyword search over conversation history
- Embed incoming messages with a small local embedding model (`all-MiniLM-L6-v2` via ONNX Runtime on Jetson)
- Vector index (SQLite-vss or standalone `hnswlib`) for semantic similarity search
- Retrieval: hybrid keyword + semantic, top-k relevant turns injected into context
- Summarization pipeline: rolling summarization of conversations >N turns
- Context window budget tracked per request — retrieval fills budget, prioritizing recent + relevant

### Dependencies
- Phase 0 (SQLite store must have full conversation history)
- ONNX Runtime for Jetson arm64
- Embedding model (~22M params, ~80MB)

### Success Metrics
- Relevant history retrieved for >90% of requests (evaluated by human review)
- Context token usage reduced >40% vs. full-history injection
- Summarization preserves key facts (factual retention >95% on test conversations)

### Timeline
4–5 weeks

---

## Phase 4 — API Plan Optimizer

### Goals
- Track daily/hourly rate limits across all providers in real-time
- Optimize request scheduling to maximize quota utilization
- Minimize cost while respecting quality constraints

### Technical Approach
- Rate limit tracker polls provider usage endpoints (or infers from 429 responses)
- Per-provider state machine: `available` → `throttled` → `exhausted` → `reset`
- Budget engine: configurable daily spend cap, per-provider caps, priority queues
- Time-aware routing: defer non-urgent requests to off-peak windows when limits reset
- Batch optimization: group similar small requests into single calls where providers support it
- Expose plan status via MCP tools and CLI: `log budget status`, `log quota --provider openai`

### Dependencies
- Phase 2 (router must respect optimizer signals)
- Provider rate limit documentation and usage endpoints

### Success Metrics
- Zero involuntary rate-limit errors (429s from client code)
- Daily quota utilization >90% of purchased limits
- Cost within 5% of daily budget target

### Timeline
3–4 weeks

---

## Phase 5 — Self-Improving Daemon

### Goals
- Always-on background process that improves system performance during idle time
- RL-based improvement of PII detection accuracy
- Distillation: use API responses as training signal for local model improvements

### Technical Approach
- Daemon process managed by systemd (or equivalent) with watchdog
- Idle detection: no requests for >N seconds → trigger self-improvement tasks
- **PII feedback loop:**
  - Log redaction confidence scores from local LLM
  - Flag low-confidence detections for review
  - Use confirmed PII (user-corrected or validated) as fine-tuning data
  - Scheduled fine-tuning jobs on Jetson (LoRA adapters for incremental updates)
- **Response distillation:**
  - Log (redacted) request → provider response pairs
  - When local model capability allows, train on distillation objective: make local model approximate provider quality
  - Budget: only use a small % of API calls for distillation data collection
- **Health monitoring:** `log daemon status`, metrics exposed via MCP

### Dependencies
- Phase 1 (local LLM redaction)
- Phase 3 (conversation history for training data)
- Sufficient Jetson VRAM to run inference + background training (or stagger)

### Success Metrics
- PII F1 improves >0.02/month from self-supervised feedback
- Local model quality (on distillation tasks) improves measurably over time
- Daemon runs with <5% CPU and <500MB RAM baseline

### Timeline
5–7 weeks

---

## Phase 6 — Multi-Agent Ecosystem

### Goals
- Crew of specialized agents collaborating via local message bus
- Tiered knowledge store with hot/warm/cold layers and confidence scoring
- Vector search over knowledge base

### Technical Approach
- **Agent roles:**
  - **Redactor** — PII detection and redaction (evolved Phase 1 model)
  - **Router** — provider selection and request routing (evolved Phase 2)
  - **Archivist** — memory management, summarization, knowledge extraction (evolved Phase 3)
  - **Optimizer** — budget, quota, scheduling, self-improvement orchestration (evolved Phase 4+5)
- **Message bus:** SQLite-based pub/sub with typed message schemas (inspired by AutoClaw)
  - Each agent subscribes to relevant event types
  - Message persistence for replay and debugging
- **Knowledge store tiers:**
  - **Hot** — in-memory, current session context, confidence >0.9
  - **Warm** — SQLite + vector index, recent days, confidence 0.5–0.9
  - **Cold** — compressed/archived SQLite, older data, confidence <0.5
- **VectorDB:** `sqlite-vec` or `hnswlib` over knowledge embeddings
- **Crew lifecycle:** `log crew start`, `log crew health`, `log crew stop`
- **Autonomous work:** when no user requests, agents work on knowledge gaps, stale entries, model improvement tasks

### Dependencies
- Phases 1–5 complete
- Message bus implementation (lightweight, SQLite-backed)
- Sufficient Jetson resources for concurrent agent processes

### Success Metrics
- End-to-end request processing through agent pipeline <500ms (P95)
- Knowledge retrieval recall >0.85 on test queries
- System handles >10 req/s sustained without degradation

### Timeline
6–8 weeks

---

## Phase 7 — Autonomous Intelligence

### Goals
- System that discovers novel PII patterns without human labeling
- Federated privacy insights (learn from patterns across deployments without sharing raw data)
- Hardware-optimized inference pipeline fully utilizing Jetson capabilities

### Technical Approach
- **Novel PII discovery:**
  - Anomaly detection on redaction confidence distribution
  - Clustering of low-confidence entities to propose new PII categories
  - Human-in-the-loop validation for proposed new patterns
  - Automatic pattern generation → fine-tuning pipeline
- **Federated learning (opt-in):**
  - Share model gradients (not data) across LOG-mcp instances
  - Differential privacy guarantees (ε-budget per contribution)
  - Central aggregation server (or peer-to-peer with TLS)
  - Each deployment benefits from collective privacy intelligence
- **Hardware optimization:**
  - TensorRT-LLM engine compilation for Jetson Orin (INT8/FP8 quantization)
  - Pipeline parallelism: redaction + embedding + routing on concurrent GPU streams
  - Memory-mapped model loading for fast agent switching
  - Power-aware scheduling: throttle background tasks when thermal limits approach

### Dependencies
- Phase 6 stable
- Federated learning infrastructure (aggregation server)
- Jetson Orin with JetPack 6+ for latest TensorRT support

### Success Metrics
- Discovers >5 novel PII patterns per quarter validated by users
- Federated model improves PII F1 >0.03 over solo training
- Full inference pipeline (redact → route → respond) <300ms P95 on Jetson
- System runs 24/7 within Jetson thermal envelope (no throttling under sustained load)

### Timeline
8–12 weeks

---

## Dependency Graph

```
Phase 0 ──┬── Phase 1 ──────────────────── Phase 5 ──── Phase 6 ──── Phase 7
          ├── Phase 2 ── Phase 4 ────────┘           │
          └── Phase 3 ────────────────────────────────┘
```

Phases 1, 2, and 3 can proceed in parallel after Phase 0. Phase 4 depends on Phase 2. Phase 5 depends on Phase 1. Phase 6 depends on all prior phases. Phase 7 depends on Phase 6.

---

## Total Estimated Timeline

| Phase | Duration | Parallel? |
|-------|----------|-----------|
| 0 — MVP | Done | — |
| 1 — Local LLM Redaction | 4–6 wk | Yes |
| 2 — Intelligent Router | 3–4 wk | Yes |
| 3 — Memory & Context | 4–5 wk | Yes |
| 4 — API Plan Optimizer | 3–4 wk | After 2 |
| 5 — Self-Improving Daemon | 5–7 wk | After 1 |
| 6 — Multi-Agent Ecosystem | 6–8 wk | After all |
| 7 — Autonomous Intelligence | 8–12 wk | After 6 |

**Critical path (parallel execution):** ~26–36 weeks from now to Phase 7 completion.
