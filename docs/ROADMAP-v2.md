# ROADMAP-v2.md — Personal AI Gateway

*Plan for the "personal AI gateway" vision. Not a contract.*

---

## Phase 0: Foundation (Refactor)

**Delivers:** Clean architecture ready for gateway features. The existing PII engine works, but it's two separate systems (CF Worker + local vault) with overlapping responsibilities.

**Work:** Consolidate PII logic into the local vault only — the Worker becomes a thin routing proxy, not a second dehydration engine. Add SQLCipher encryption to the SQLite vault. Create the `.env` configuration layer. Write integration tests that prove PII never leaks to cloud.

**Dependencies:** None (we have the code).

**Effort:** 1–2 weeks.

**Done when:** Worker has zero PII logic (delegates to local). Vault is encrypted. `docker compose up` gives a working system. All existing tests pass.

---

## Phase 1: Tunnel Gateway

**Delivers:** A chat interface accessible from anywhere, routed through your hardware. No port forwarding needed.

**Work:** Configure CF Tunnel as a first-class service (not optional). Add WebSocket support to the Worker for streaming. Build a minimal chat UI (single HTML page served from CF Pages or Worker static assets). Add JWT auth with passphrase-based login.

**Dependencies:** Phase 0 complete.

**Effort:** 2–3 weeks.

**Done when:** User can open `my-gateway.pages.dev`, log in, chat, and get responses routed through their local machine. Works on phone browser.

---

## Phase 2: Local-First Intelligence

**Delivers:** Most requests handled locally. Cloud API is a safety net.

**Work:** Add Ollama as a core dependency (not optional profile). Build the routing classifier — lightweight model scores request complexity. If confidence >0.7, route to local Ollama; otherwise fall back to cloud. Add routing metrics to `/stats`. Default model: phi3:mini or gemma2:2b (quantized).

**Dependencies:** Phase 1 (need the gateway to route through).

**Effort:** 3–4 weeks.

**Done when:** >60% of typical requests served locally. Local latency <500ms. Cloud fallback works seamlessly.

---

## Phase 3: Memory & Context

**Delivers:** The assistant remembers your conversations and uses context intelligently.

**Work:** Add FTS5 and sqlite-vec to the vault. Embed messages with a small local model (all-MiniLM-L6-v2). Build hybrid retrieval (keyword + semantic) and inject relevant history into context window. Implement rolling summarization for long conversations. Expose via MCP tool `query_history`.

**Dependencies:** Phase 2 (need local inference for embeddings and summarization).

**Effort:** 3–4 weeks.

**Done when:** Assistant references prior conversations accurately. Context injection is token-efficient (<40% of window used for history).

---

## Phase 4: Self-Improvement Loop

**Delivers:** The system gets better at serving you specifically, without manual intervention.

**Work:** Build the training daemon (systemd service). When cloud API produces a response the local model couldn't, save the (redacted) pair to `training_queue`. Background LoRA fine-tuning on Jetson/PC using unsloth or similar. Track adapter versions in `model_versions`. Auto-promote if metrics improve, auto-rollback if they degrade. Rate-limit training to avoid burning hardware.

**Dependencies:** Phase 2 + 3 (need local model, routing data, conversation history for training signal).

**Effort:** 4–6 weeks.

**Done when:** Local inference accuracy measurably improves week-over-week. Adapter versions are tracked. User never sees training happen.

---

## Phase 5: Multi-Device

**Delivers:** Phone, tablet, laptop — all route through the same gateway with shared context.

**Work:** Extend the chat UI as a PWA. Add device registration to `user_preferences`. Implement session continuity (resume conversation from any device). Optimize the tunnel for multiple concurrent WebSocket connections. Consider a lightweight companion app (Tauri or React Native) for native UX.

**Dependencies:** Phase 1 (gateway), Phase 3 (shared memory).

**Effort:** 3–4 weeks.

**Done when:** User can start a conversation on laptop, continue on phone, with full context. 3+ concurrent devices supported.

---

## Phase 6: Ecosystem

**Delivers:** Community improvements without sharing data. Plugin system for extending capabilities.

**Work:** Design a plugin API (MCP-based tools). Create a registry format for sharing fine-tuned adapters (weights only, no data). Implement optional federated learning — share gradients (ε-differential privacy) across deployments. Build a model marketplace where users can download community adapters that improve specific capabilities (coding, writing, domain knowledge).

**Dependencies:** Phase 4 (adapter versioning), Phase 5 (multi-device stability).

**Effort:** 6–8 weeks.

**Done when:** User can install a community adapter with one command. Federated improvements are opt-in and privacy-preserving. Plugin ecosystem has 5+ working examples.

---

## Critical Path

```
Phase 0 → Phase 1 → Phase 2 → Phase 3 → Phase 4
                                        ↘ Phase 5 → Phase 6
```

Phases 3–5 can overlap once Phase 2 is stable. Phase 6 is the long tail.

**Total estimate:** 22–31 weeks from now to Phase 6 MVP.
