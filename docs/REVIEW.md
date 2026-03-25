# REVIEW.md — Architecture Review

*Brutal honest feedback from a principal engineer. Read this before writing more code.*

---

## Issues Found

### Tensions

**1. PII location: Worker does it now, ARCHITECTURE says local-only, but IMPLEMENTATION Phase 0 keeps both.**
VISION and ARCHITECTURE say "Worker becomes thin proxy, all PII local." But IMPLEMENTATION §6 says "Worker continues to work independently during Phase 0" and "Both Worker and local gateway can coexist." This is fence-sitting. Two PII engines means two attack surfaces, two codebases to maintain, and subtle divergence bugs.
**Fix:** Phase 0 must commit: Worker strips zero PII. If the tunnel is down, the request fails. Period.
**Blocks Phase 0:** Yes.

**2. VISION says "fork, deploy, configure tunnel — done." ROADMAP says 22-31 weeks.**
These are not the same product. The VISION promises Day 1 simplicity; the ROADMAP delivers Day 365 complexity after 6 phases. The Day 1 experience described in VISION requires Phase 1-2 at minimum (tunnel + local inference), not just Phase 0.
**Fix:** Rewrite VISION's Day 1 to match what Phase 0 actually delivers: "Fork, docker compose up, chat locally via browser." Tunnel and cloud fallback are Day 7+, not Day 1.

### Gaps

**3. Nobody defined what "dehydrated" means for the LLM.**
Replacing "John Smith" with `<ENTITY_1>` destroys semantic meaning. A model asked "What did John say about the budget?" receives "What did <ENTITY_1> say about the budget?" — and will hallucinate or produce garbage. This is a fundamental unsolved problem that the entire architecture depends on.
**Fix:** Use descriptive pseudonyms, not opaque tokens: `[PERSON_A]`, `[EMAIL_B]`. Add a system prompt preamble that explains the entities: "ENTITY_1 refers to a person whose name is withheld." Test this thoroughly.
**Blocks Phase 0:** No (cloud fallback masks it), but blocks Phase 2 when local inference matters.

**4. No error handling strategy.**
What happens when Ollama OOMs? When the tunnel drops mid-stream? When SQLite hits a lock? When the cloud API returns 429? ARCHITECTURE shows happy paths only.
**Fix:** Add a "Failure Modes" section to ARCHITECTURE. Gateway must: retry cloud on local failure (with backoff), buffer WebSocket messages during reconnects, and show degradation status in the UI.

### Over-Engineering

**5. Training pipeline (Phase 4) is 4-6 weeks for something no one has validated users want.**
LoRA fine-tuning, adapter versioning, auto-promote/rollback — this is a full MLOps system. We don't even know if users will run this long enough to generate useful training data.
**Fix:** Cut Phase 4 entirely from the near-term plan. Replace with "export conversation data as JSONL" — let power users handle their own fine-tuning. Revisit after 50+ active users.

**6. Federated learning and model marketplace (Phase 6).**
Differential privacy, gradient sharing, adapter registries — this is a research project, not a product feature.
**Fix:** Delete Phase 6 from the roadmap. Add a vague "Phase 6: TBD based on user demand."

### Under-Engineering

**7. No rate limiting or abuse protection on the local gateway.**
The Starlette server binds to port 8000 but there's no mention of rate limiting, request size limits, or connection caps. One runaway client can OOM your Ollama.
**Fix:** Add `slowapi` or similar to `gateway/server.py`. Cap concurrent requests. Limit message size to 10KB.
**Blocks Phase 0:** Yes.

**8. JWT with hardcoded secret (IMPLEMENTATION §6, step 6).**
"Hardcoded secret for now" will ship and never be fixed. A single secret means no key rotation, no multi-device auth.
**Fix:** Generate a random secret on first run, store in vault. Add a `/auth/rotate` endpoint.
**Blocks Phase 0:** Soft block — don't ship without it.

### Ordering Problems

**9. Phase 0 creates new DB tables (`training_queue`, `model_versions`) that nothing uses until Phase 4.**
Adding unused tables during a refactor is a magnet for bugs and confusion.
**Fix:** Phase 0 adds only `user_preferences`. `training_queue` and `model_versions` come with their respective phases.

### Naming Confusion

**10. Three names, zero clarity.**
Code is `LOG-mcp`, VISION calls it "personal AI gateway", and suggests "Gatekeeper" or "Mynd" as alternatives. The repo is `LOG-mcp`, the Docker service is `vault`, the HTTP server is `gateway`. Pick one.
**Fix:** Keep `LOG-mcp` as the repo/code name. The product-facing name is `LOG-mcp` for now — stop suggesting alternatives until someone actually brands it. Rename Docker service from `vault` to `log-mcp` to match.

### Onboarding Gaps

**11. No mention of "what if the user doesn't have Docker?"**
The onboarding assumes Docker fluency. Most people who want an AI assistant don't know what a container is.
**Fix:** Add a `get-started.md` that covers: prerequisites, what each command does, and troubleshooting the three things that will actually break (Docker not installed, Ollama model not pulled, port already in use).

### Security Holes

**12. WebSocket auth passes JWT in query string.**
`wss://gateway/ws?token=<jwt>` means the token appears in server logs, proxy logs, and browser history.
**Fix:** Send JWT in the first WebSocket message after connection, not in the URL.

**13. Encryption key derived from passphrase — but where is the passphrase entered?**
ARCHITECTURE says Argon2id derivation. IMPLEMENTATION says "hardcoded secret for now." Neither says how the user sets or enters the vault passphrase. This is the entire security model and it's undefined.
**Fix:** Phase 0: vault starts unencrypted, prints a warning. Add `log-mcp vault encrypt` CLI command for Phase 1. Document the flow clearly.

---

## Consolidated Recommendations

### Name Decision
Keep **LOG-mcp**. Stop proposing alternatives. The repo name is set, the acronym is fine, and renaming mid-build is a waste of motion. Revisit branding after the product works.

### Phase 0 Scope

**In:**
- `vault/config.py` — Pydantic settings
- Schema dedup in `core.py`/`reallog_db.py`
- `gateway/server.py` — Starlette with `/v1/chat/completions`
- `web/index.html` — Single-file chat UI
- Docker Compose v2
- PII leakage integration tests
- Rate limiting on gateway
- JWT auth (generated secret, not hardcoded)

**Out:**
- `training_queue` and `model_versions` tables
- Worker refactor (keep it working as-is)
- Any tunnel changes
- Router, memory, trainer modules

### True Minimum Viable Product
**Day 1:** User runs `docker compose up`. Opens `localhost:8000`. Logs in. Sends a message. It gets dehydrated locally, proxied to cloud API, rehydrated, and displayed. That's it. No tunnel. No local LLM. No streaming. Just a private proxy with a chat UI. This proves the core value prop: your data stays local.

### 3 Things to Cut to Ship Faster
1. **WebSocket streaming** — HTTP POST is fine for Phase 0. Users can wait 2 seconds.
2. **Phase 4 training pipeline entirely** — replace with JSONL export.
3. **Phase 6 ecosystem/federated learning** — pure fantasy at this stage.

### 3 Things Not to Cut
1. **PII leakage tests** — this is the whole point. Ship without tests and you've built nothing.
2. **SQLCipher encryption** (even as opt-in) — the vault is the crown jewel. An unencrypted vault on a Jetson is a liability.
3. **Rate limiting** — one OOM from an unbounded request and the user blames your software, not Ollama.

---

*This review is meant to save you weeks of rework. The biggest risk isn't technical — it's building 6 phases of architecture for a product nobody has used yet. Ship Phase 0. Get feedback. Then decide what Phase 1 actually needs to be.*
