# PHASE2-PLAN.md — Personal Intelligence Layer: Execution Plan v2

*Script-based instant routing + cheap API fires first + optional escalation. Zero added latency.*

---

## Architecture Change from v1

**v1 (old):** Message → 650ms ML router → then send. User waits.
**v2 (new):** Message → **instant send** to cheap API (~50ms) → parallel script classifies → optional escalation to better model → swap/offer if warranted.

The routing script is **rules, not ML**. The local ML system optimizes the script over time, but never blocks a request.

---

## Request Flow

```
User message
  → PII dehydration (existing, ~10ms)
  → Instant-fire to cheap model (DeepSeek-chat, ~0ms delay)
  → Parallel: routing script classifies (~5ms, regex-based)
     → CHEAP_ONLY: done, show response
     → ESCALATE: fire better model (DeepSeek-reasoner)
        → if better response arrives first: show it
        → if cheap response already shown: offer swap
  → Parallel: local rewriter notes preferences for next call
  → Store interaction with routing decision + feedback
  → User sees response(s) → 👍/👎/critique
```

## Resolved Decisions

| Decision | Resolution |
|---|---|
| Primary routing | Rule-based script (regex/pattern matching, ~5ms) |
| ML role | Background optimizer: updates routing rules based on feedback |
| Default send mode | Instant-send ON: cheap model fires immediately |
| Cheap model | DeepSeek-chat (or any fast API) |
| Escalation model | DeepSeek-reasoner (for hard reasoning tasks) |
| Local inference | Optional, runs in parallel for learning, not blocking |
| User controls | Settings panel: instant-send, parallel mode, privacy mode, manual overrides |
| Escalation patterns | Regex triggers + heuristics (see routing script below) |
| Fallback | If cheap API fails → try escalation model directly |
| Feedback storage | interactions table with feedback + critique |
| Search | FTS5 keyword only; embeddings deferred |

---

## Routing Script

```python
# vault/routing_script.py — Rule-based, ~5ms, no ML needed
# The ML optimizer can add/modify rules over time

RULES = {
    "CHEAP_ONLY": [
        r"what (is|are|was|were)\b",
        r"how (many|much|old|far|long|big)\b",
        r"convert \d+\s*\w+\s*(to|into)\b",
        r"define\b",
        r"calculate\b",
        r"translate\b",
        r"spell\b",
        r"synonym|antonym\b",
        r"(sum|list|count|show)\b.*\b(all|my|the)\b",
    ],
    "ESCALATE": [
        r"(debug|traceback|error|exception|fix my)\b",
        r"(write|create|draft|compose)\b.*(code|essay|article|story|letter|email)\b",
        r"(explain|analyze|compare|contrast)\b.{20,}",
        r"(complex|advanced|expert|detailed)\b",
        r".*\b(code|function|class|algorithm)\b.{50,}",  # long code request
        r"(plan|design|architect)\b",
        r"(review|critique|improve)\b.*(my|this|the)\b",
    ],
    "MANUAL_OVERRIDE": [  # user typed /local, /cloud, /reason, /compare
        r"^/(local|cloud|reason|compare)\b",
    ],
}

def classify(user_input: str, message_length: int, has_code_blocks: bool) -> dict:
    """Classify in ~5ms. Returns routing decision."""
    text = user_input.strip().lower()
    
    # Manual overrides first
    for pattern in RULES["MANUAL_OVERRIDE"]:
        if re.search(pattern, text):
            cmd = text.split()[0].lstrip("/")
            return {"action": cmd, "reason": "manual override"}
    
    # Check escalation patterns (specific beats general)
    for pattern in RULES["ESCALATE"]:
        if re.search(pattern, text):
            return {"action": "ESCALATE", "reason": "pattern matched"}
    
    # Heuristics: long messages or code blocks → escalate
    if message_length > 500:
        return {"action": "ESCALATE", "reason": "long message"}
    if has_code_blocks:
        return {"action": "ESCALATE", "reason": "contains code blocks"}
    
    # Default: cheap model is fine
    return {"action": "CHEAP_ONLY", "reason": "default"}
```

---

## New DB Tables

```sql
CREATE TABLE IF NOT EXISTS interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    user_input TEXT NOT NULL,
    rewritten_input TEXT,
    route_action TEXT NOT NULL,        -- CHEAP_ONLY / ESCALATE / local / cloud / reason
    route_reason TEXT,
    target_model TEXT NOT NULL,
    response TEXT NOT NULL,
    escalation_response TEXT,          -- NULL or better model response
    response_latency_ms INTEGER,
    escalation_latency_ms INTEGER,
    feedback TEXT DEFAULT NULL,        -- up / down / NULL
    critique TEXT DEFAULT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_interactions_route ON interactions(route_action);
CREATE INDEX IF NOT EXISTS idx_interactions_feedback ON interactions(feedback);
```

---

## New Config Variables

| Variable | Default | Purpose |
|---|---|---|
| `instant_send` | `true` | Fire cheap model immediately |
| `cheap_model_endpoint` | `https://api.deepseek.com/v1/chat/completions` | Cheap/fast API |
| `cheap_model_name` | `deepseek-chat` | Model name for cheap API |
| `escalation_model_endpoint` | same as cheap | Quality API endpoint |
| `escalation_model_name` | `deepseek-reasoner` | Model name for escalation |
| `ollama_base_url` | `http://localhost:11434` | Ollama for local inference |
| `parallel_mode` | `false` | Run local + cloud simultaneously |
| `privacy_mode` | `true` | PII-strip before ANY cloud call |

---

## Task List (12 tasks)

### Task 1: Config — new routing variables
**Files:** `vault/config.py`
**Do:** Add all new config vars to `VaultSettings`. Defaults as above. Env prefix `LOG_`.
**Acceptance:** `VaultSettings()` works with all new fields.
**Depends on:** Nothing.

### Task 2: DB migration — interactions table + indexes
**Files:** `vault/core.py`
**Do:** Add `interactions` table to `_init_db()`. Add methods: `add_interaction()`, `update_feedback()`, `get_stats()`.
**Acceptance:** Fresh DB has table, can insert/query.
**Depends on:** Task 1.

### Task 3: Routing script
**Files:** `vault/routing_script.py` (new)
**Do:** Rule-based classifier as designed above. `classify(user_input, message_length, has_code_blocks) -> dict`. Pure regex, no external deps, ~5ms. Include unit tests in `tests/test_routing_script.py` with 20+ test cases covering every rule and edge cases.
**Acceptance:** classify("what is 5km in miles") returns CHEAP_ONLY. classify("debug my traceback") returns ESCALATE. 20+ tests pass.
**Depends on:** Nothing.

### Task 4: Preference seed + CRUD
**Files:** `vault/core.py`, `gateway/routes.py`
**Do:** Seed 5 defaults in user_preferences. Add RealLog methods: get_preferences(), set_preference(), delete_preference(). Add API routes: GET/POST/DELETE /v1/preferences.
**Acceptance:** Fresh DB has defaults. CRUD works via API.
**Depends on:** Task 2.

### Task 5: Dual-model chat pipeline
**Files:** `gateway/routes.py`
**Do:** Refactor chat_completions: (1) dehydrate, (2) run routing script, (3) instant-fire cheap model, (4) if ESCALATE → fire escalation model in parallel, (5) rehydrate responses, (6) store interaction, (7) return with routing metadata. On instant_send=false, wait for script first. On cheap failure, fallback to escalation. Add X-Route-Action and X-Target-Model response headers.
**Acceptance:** Simple question → cheap model only. Hard question → both fire. Response includes routing metadata.
**Depends on:** Tasks 2, 3.

### Task 6: Feedback API
**Files:** `gateway/routes.py`
**Do:** POST /v1/feedback (interaction_id + feedback). POST /v1/feedback/critique (+ critique text). Both auth-protected.
**Acceptance:** Can post feedback, stored in DB.
**Depends on:** Task 2.

### Task 7: Chat UI — routing badge + instant feedback
**Files:** `web/index.html`
**Do:** Show route badge on each response (⚡ fast / 🧠 deep). Show target model name. Add 👍/👎 buttons that call feedback API. After 👎, show critique text input. Store interaction_id in message metadata.
**Acceptance:** Badges visible. Feedback buttons work. Critique submits.
**Depends on:** Tasks 5, 6.

### Task 8: Chat UI — settings panel
**Files:** `web/index.html`
**Do:** Add ⚙️ settings panel: toggle instant-send, parallel mode, privacy mode, default model. Show current routing rules. Save preferences via /v1/preferences.
**Acceptance:** Settings persist. Changes reflected in next message.
**Depends on:** Tasks 4, 7.

### Task 9: Health + degradation
**Files:** `gateway/routes.py`
**Do:** GET /v1/health → {cheap: bool, escalation: bool, ollama: bool}. Cache statuses. If cheap model down, use escalation. If both down, 503.
**Acceptance:** Health endpoint accurate. Graceful degradation works.
**Depends on:** Task 5.

### Task 10: Privacy mode enforcement
**Files:** `gateway/routes.py`
**Do:** When privacy_mode=true (default), ALWAYS dehydrate before any cloud call. When false, offer "send raw" option in UI. Add privacy indicator in UI header.
**Acceptance:** No PII reaches cloud in default mode. Toggle works.
**Depends on:** Tasks 5, 8.

### Task 11: All tests
**Files:** `tests/test_routing_script.py` (new), `tests/test_phase2.py` (new)
**Do:** 20+ routing script tests (every rule, edge cases). E2e test with mocked APIs: simple→cheap, complex→escalate, feedback loop, degradation.
**Acceptance:** All tests pass.
**Depends on:** Tasks 3, 5, 6.

### Task 12: Docker + E2E verification
**Files:** `docker/Dockerfile`, `requirements.txt`
**Do:** Update Docker image. docker compose up → test full flow via curl. Verify instant-send, escalation, feedback, settings.
**Acceptance:** Docker builds and runs. All endpoints verified.
**Depends on:** All above.

---

## Build Order

```
1 → 2 → (3, 4 parallel) → 5 → (6, 9 parallel) → 7 → 8 → 10 → 11 → 12
```

## Deferred
- Compare mode UI (P3)
- LoRA fine-tuning (Phase 3)
- Semantic search / embeddings (Phase 3)
- ML routing optimizer (Phase 3 — updates the script, doesn't replace it)
- Threshold auto-tuning
- /critique auto-parsing
