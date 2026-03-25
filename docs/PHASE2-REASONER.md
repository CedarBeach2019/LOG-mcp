# PHASE2-REASONER.md — Personal Intelligence Layer: Technical Analysis

*Target: Jetson Super Orin Nano (8GB VRAM, 2TB NVMe), qwen3.5:2b via Ollama, DeepSeek API (chat + reasoner).*

---

## A. Router Classification

**Categories with concrete examples:**

| Class | Definition | Example | Route |
|-------|-----------|---------|-------|
| `FACTOID` | Single-answer lookup | "What's 5km in miles?" | Local |
| `COMMAND` | System action request | "List my vault stats" | Local |
| `REWRITE` | Formatting/editing | "Make this email shorter" | Local |
| `REASONING` | Multi-step logic | "Debug this traceback: [paste]" | DeepSeek-reasoner |
| `CREATIVE` | Open-ended generation | "Write a cover letter for..." | DeepSeek-chat |
| `CONTEXT` | Requires conversation memory | "What did we decide about the API last week?" | Local (memory) + cloud (generate) |
| `AMBIGUOUS` | Could go either way | "Help me with Python" | Cloud (safer default) |

**Router prompt format** (structured output for 2B reliability):

```
Classify this message. Reply ONLY with JSON: {"class":"FACTOID","confidence":0.9,"reason":"single unit conversion"}

Message: {user_input}
Session length: {n} messages
Has memory hits: {bool}
```

**Latency estimate:** qwen3.5:2b Q4_K_M on Jetson Super Orin generates ~30 tokens/sec. The router prompt produces ~20 output tokens → **~650ms** including prompt processing. This is acceptable for classification (target: <1s added latency).

**Fine-tuning vs prompt-based:** Start prompt-based. The 2B model handles structured classification well with few-shot examples. Fine-tune only if accuracy <80% after 500 classified messages. LoRA on 2B costs ~200MB and 15 minutes on Jetson for a 500-example dataset — trivial overhead.

**When the router is wrong:** Always allow user override via `/local` and `/cloud` commands. Log misclassifications to `training_queue` with `router_was_wrong=1`. After 20+ misclassifications of the same type, auto-adjust confidence thresholds for that category.

---

## B. Prompt Rewriting Engine

**How it works:** After classification, the local model receives a second prompt to transform the user's input into an optimized version for the target model.

**Rewriter prompt:**

```
Rewrite this message for {target_model}. The user prefers: {preferences_json}.
Original: {user_input}
Context from memory: {top_3_memory_hits if any}

Output only the rewritten message, nothing else.
```

**Per-model preference examples:**

1. DeepSeek-chat: User prefers "concise bullet-point responses" → rewriter adds "Respond in bullet points, max 200 words" to system instruction.
2. DeepSeek-reasoner: User prefers "show your work" → rewriter adds "Explain each reasoning step" to user message.
3. Local model: User prefers "casual tone" → rewriter expands abbreviations, removes formality markers before local handling.
4. DeepSeek-chat: User dislikes "AI disclaimers" → rewriter appends "Do not include disclaimers or hedging about being an AI."
5. DeepSeek-reasoner: User is a programmer → rewriter prepends relevant tech stack from user profile to system context.

**Information the rewriter operates on:**
- **Adds:** User preferences (from `user_preferences` table), relevant memory context, model-specific system instructions.
- **Restructures:** Vague requests get specificity injected ("help with code" → "help with Python async/await debugging" based on recent context).
- **Removes:** Redundant context already captured in memory, excessive pleasantries (token waste on cloud API).

**Measuring improvement:** Store the original input + rewritten input + response. When user gives thumbs-down, check if the rewrite lost information. Metric: rewrite_retention_rate = (thumbs_up where rewritten) / (total rewritten). Target: >70%.

---

## C. Preference Learning Pipeline

**Data schema per interaction:**

```sql
CREATE TABLE interactions (
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL,
  user_input TEXT NOT NULL,          -- Original (PII-stripped)
  rewritten_input TEXT,              -- After local rewriter
  router_class TEXT,                 -- FACTOID, REASONING, etc.
  router_confidence REAL,
  target_model TEXT,                 -- local / deepseek-chat / deepseek-reasoner
  response TEXT NOT NULL,            -- Model output (PII-stripped)
  response_latency_ms INTEGER,
  feedback TEXT DEFAULT NULL,        -- up / down / NULL
  critique TEXT DEFAULT NULL,        -- User's /critique text
  memory_context_ids TEXT,           -- JSON array of retrieved memory IDs
  created_at TEXT DEFAULT (datetime('now'))
);
```

**Thumbs up/down → improvement:**
- `thumbs_up`: Store `(rewritten_input, response)` in `training_queue` with `quality_score=1.0`. This is a positive example for the target model class.
- `thumbs_down`: Store with `quality_score=0.0`. Also trigger the `/critique` prompt: "What was wrong with this response? Be specific."
- After 50+ interactions with feedback, cluster thumbs-down by `router_class`. If one class has >40% negative rate, increase confidence threshold for routing that class to cloud.

**`/critique` workflow:**
1. User sends `/critique That response was too verbose and missed the point about async`
2. Local model parses the critique, extracts: `{problem: "verbose", missed_point: "async", suggestion: "be concise"}`
3. Structured feedback stored in `interactions.critique` and also as a preference rule: `deepseek-chat + REASONING class → "be concise, focus on async patterns"`
4. Preference rule gets injected into future rewriter prompts for that class/model combination.

**Cold start (day 1):**
- Default preferences: concise responses, no disclaimers, casual tone.
- Router uses fixed confidence thresholds (0.7 for local, everything else → cloud).
- No memory context injection. System works but doesn't personalize yet.
- By day 3 with ~100 interactions, preference rules emerge and memory retrieval activates.

**ML approach — NOT LoRA fine-tuning for preferences:**
- Preferences change too fast for fine-tuning cycles. Use **RAG over preference rules** instead.
- The local model retrieves relevant preference rules from `user_preferences` (key-value) and injects them into the rewriter prompt.
- LoRA fine-tuning is reserved for the **local chat model** to improve response quality on high-confidence local classes. Trigger: after 500+ positive training examples for a class, run LoRA with unsloth. Expected: 1 hour on Jetson for 2B model with 500 examples.
- Retrain schedule: Weekly, during idle hours (3-6am). Check training_queue size > 100 new examples before triggering.

---

## D. Parallel Compare Mode

**How it works:** User opt-in via `/compare` toggle. When active:
1. Router classifies message.
2. If `router_confidence < 0.9` (uncertain), send to BOTH local model AND cloud model.
3. Display responses side-by-side in chat UI with collapse/expand.
4. User clicks preferred response (or neither).

**UI presentation:**
```
┌──────────────────────────────────────┐
│ 🤖 Local Response                     │
│ [concise answer, 180ms]               │
│                            [Pick this] │
├──────────────────────────────────────┤
│ ☁️ DeepSeek Response                   │
│ [detailed answer, 2.1s]               │
│                            [Pick this] │
└──────────────────────────────────────┘
```

**Learning signal:** Store which response was picked. Over time, the local model learns prediction: `for class X with confidence 0.6-0.8, user picks cloud 72% of the time` → auto-adjust threshold upward. This is a simple logistic regression model (10 features: class, confidence, input length, time-of-day, session_length, etc.) that runs on the local model as a structured output — not a separate ML model.

**Deferred:** The local model predicting user preference (the "judger") is a nice-to-have. The threshold adjustment based on pick rates is sufficient for MVP.

---

## E. Context Management

**What gets stored (per session):**
- All messages (PII-stripped) → `messages` table (existing).
- Session summary (generated by local model at session end) → `sessions.summary`.
- Extracted entities/facts from conversation → new `memory_facts` table:
  ```sql
  CREATE TABLE memory_facts (
    id INTEGER PRIMARY KEY,
    session_id TEXT,
    fact TEXT NOT NULL,          -- "User is working on LOG-mcp project"
    embedding BLOB,             -- sqlite-vec float32 array
    fact_type TEXT,             -- project / preference / relationship / decision
    confidence REAL DEFAULT 0.8,
    created_at TEXT,
    access_count INTEGER DEFAULT 0,
    last_accessed TEXT
  );
  ```

**Summarization:** At session end, local model generates a 3-5 sentence summary. After 50 messages in a session, generate rolling summary every 20 messages. Old messages are kept in DB but not injected into context (only summary + recent 10 messages).

**Context preparation differs by target:**
- **Local model:** System prompt + recent 5 messages + top 3 memory facts + user preferences. Budget: ~1500 tokens.
- **Cloud model:** System prompt + preamble + recent 10 messages + top 5 memory facts + user preferences + rewriter instructions. Budget: ~4000 tokens (DeepSeek supports 64K, but we minimize cloud token usage).

**Semantic search:** Use `sentence-transformers/all-MiniLM-L6-v2` (22MB model) for embeddings via Ollama's `/api/embeddings`. Index in sqlite-vec (already in ARCHITECTURE.md spec). For <10K facts, brute-force cosine similarity is fast enough. FTS5 for keyword fallback. Hybrid: semantic results weighted 0.7, keyword 0.3, deduplicated.

---

## F. Practical Concerns

**VRAM budget (8GB total):**
| Component | VRAM |
|-----------|------|
| qwen3.5:2b Q4_K_M | ~1.4GB |
| KV cache (4K context) | ~0.5GB |
| MiniLM-L6-v2 embeddings | ~0.1GB |
| CUDA/Orin overhead | ~0.5GB |
| **Total** | **~2.5GB** |
| **Free for LoRA training** | **~5.5GB** |

Plenty of headroom. We can load a second model (e.g., a 4B model) during idle hours for training without eviction.

**Inference + training simultaneously:** LoRA fine-tuning on 2B with unsloth uses ~3-4GB. During training, inference still works on the remaining VRAM (KV cache grows dynamically). If OOM risk, pause training on inference request and resume after. Implement a simple lock in the training daemon.

**Disk usage:** Training data at ~1KB/interaction. 10K interactions = ~10MB. Model checkpoints (LoRA adapters): ~50MB each, keep last 5 versions = 250MB. Embedding index: ~50MB per 10K facts. Total after a year of heavy use: <2GB. The 2TB NVMe is not a concern.

**Latency targets:**
- Router classification: <800ms (achievable at ~650ms estimated).
- Prompt rewriting: <1s (separate inference call, ~30 tokens output).
- Local response generation: <3s (depends on response length).
- Total local path: <5s from user input to response.
- Cloud fallback: add 1-3s network latency = 3-6s total.

---

## G. Implementation Priority

| Component | Impact | Feasibility | Score | Priority |
|-----------|--------|-------------|-------|----------|
| Router classifier | 9 | 9 | 81 | **P0** |
| Prompt rewriter | 7 | 8 | 56 | **P1** |
| Preference storage + injection | 8 | 9 | 72 | **P0** |
| Feedback (thumbs up/down) | 6 | 9 | 54 | **P1** |
| /critique parsing | 5 | 7 | 35 | P2 |
| Memory facts + embeddings | 8 | 6 | 48 | P1 |
| Compare mode | 4 | 5 | 20 | P3 |
| LoRA fine-tuning | 7 | 4 | 28 | P2 |
| Threshold auto-tuning | 5 | 6 | 30 | P2 |

**Weekend MVP (proves the concept):**
1. Router classifier with 7 categories (structured JSON output from qwen3.5:2b).
2. Route `FACTOID`/`COMMAND`/`REWRITE` locally, everything else to DeepSeek.
3. Basic prompt rewriter that injects user preferences.
4. Thumbs up/down on responses → store in `interactions` table.
5. `user_preferences` table with 5 default preference rules.
6. Display router decision in chat UI ("🤖 local" vs "☁️ cloud" badge).

This delivers the core insight: the 2B model can meaningfully triage requests and the system gets user feedback. Everything else (memory, compare mode, LoRA training) builds on this foundation.

**What can be deferred without blocking the learning loop:**
- Compare mode (P3) — nice UX but not needed for learning.
- LoRA fine-tuning (P2) — RAG over preferences works for months before fine-tuning becomes necessary.
- Semantic search (P1 but deferrable) — FTS5 keyword search is sufficient for the first 1000 facts.
- Threshold auto-tuning (P2) — fixed thresholds work fine initially; manual `/local` and `/cloud` overrides cover edge cases.

---

## Architecture: Phase 2 Request Flow

```
User input
  → [PII dehydration] (existing)
  → [Router: qwen3.5:2b classifies → JSON]
     → FACTOID/COMMAND/REWRITE → local
     → REASONING/CREATIVE/AMBIGUOUS → cloud
     → CONTEXT → local memory + cloud generate
  → [Rewriter: qwen3.5:2b optimizes prompt for target]
  → [Target model generates response]
  → [PII rehydration] (existing)
  → [Store in interactions table]
  → User
```

Total local overhead: 2 × qwen3.5:2b inference calls (~1.5s combined) + memory retrieval (~50ms). This is the "intelligence tax" for having a smart gateway. Worth it.
