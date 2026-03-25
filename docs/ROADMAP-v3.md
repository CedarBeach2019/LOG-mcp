# ROADMAP-v3.md — From Gateway to Agent Infrastructure

## The Thesis

LOG-mcp is not a PII tool, a chat proxy, or a router. It's **model selection infrastructure**.

Every AI application has the same unsolved problem: which model, which configuration, which prompt strategy is best for this specific task? Today the answer is "somebody picks and hopes." LOG-mcp's draft round + ranking + feedback loop answers this automatically.

The draft round is an observation. The rankings are training signal. The preferences are learned policy. Together they're a system that gets better at choosing models the more you use it.

Phase 1 (done) proved the gateway works. Phase 2 (done) proved the feedback loop works.
Phase 3 makes it infrastructure that other agents build on.

---

## Phase 3: The Optimization Engine

**Goal:** The system doesn't just route — it learns to route better from actual outcomes.

### 3A: Feedback → Routing Updates (Week 1-2)

The routing script is regex rules. That's fast (~5ms) but static. Make it self-updating:

- **Stats collector** — cron job that queries interactions table nightly:
  - Per-route-class: avg feedback score, escalation rate, latency
  - Per-model: success rate by message type
  - Per-draft-profile: win rate when ranked
- **Routing script generator** — Python script that reads stats and outputs updated `routing_script.py` rules:
  - If `/compare` mode consistently gets 👍 for code questions → add pattern `"code"` to MANUAL_OVERRIDE
  - If cheap model gets 👍 85%+ for greetings → tighten CHEAP_ONLY patterns
  - If escalation model wins draft round 70%+ for reasoning → add pattern `"explain"` to ESCALATE
- **Dry-run preview** — admin endpoint `POST /v1/routing/update?dry=true` shows proposed changes before applying
- **Rollback** — version the routing script, keep last 10 versions

This is the key insight: **the router doesn't need ML. It needs data.** Stats-based rule updates are interpretable, debuggable, and don't risk catastrophic weirdness. Save ML for Phase 4.

### 3B: Draft Round as API (Week 2-3)

Right now drafts only work in the web UI. Expose it as a first-class API:

- `POST /v1/compare` — OpenAI-compatible wrapper around drafts:
  ```json
  {"model": "auto", "messages": [...], "n": 3, "max_tokens": 100}
  ```
  Returns 3 short responses + metadata. Consumer picks winner.
- `POST /v1/compare/{id}/rank` — submit ranking, system records and learns
- **Batch compare** — `POST /v1/compare/batch` with N prompts, returns matrix of responses. For automated evaluation.
- **SDK helper** — `pip install log-mcp` gives you:
  ```python
  from logmcp import compare
  results = compare("How do I sort a linked list?")
  ranked = results.rank()  # auto-rank by estimated quality
  full = ranked[0].elaborate()  # winner expands
  ```

This is the play: make LOG-mcp the easiest way for any developer to A/B test models. Not just for chat — for any task that calls an LLM.

### 3C: Preference Profiles (Week 3-4)

Users don't just have one preference. A developer wants concise code. A writer wants creative prose. A analyst wants structured data.

- **Named preference sets** — `POST /v1/preferences/{profile_name}` creates a preference preset
- **Auto-switching** — detect user intent and switch preference profiles:
  - Code block in message → "developer" profile
  - Long-form question → "writer" profile
  - Data table request → "analyst" profile
- **Cross-user profiles** — share preference sets between users on the same instance
- **Profile marketplace** — export/import as JSON. "Here's my optimized setup for coding."

---

## Phase 4: Local Intelligence

**Goal:** The Jetson becomes useful, not decorative.

### 4A: Ollama Integration (Week 1-2)

- **Health-aware routing** — if Ollama is running, offer local-first for CHEAP_ONLY:
  - Ollama up → route to local 2B model for simple queries
  - Ollama down → fallback to cloud cheap model
  - User sees 🔵 LOCAL badge, knows their data stayed home
- **Model pre-warming** — cache common prompts in Ollama context window
- **Draft round with mixed providers** — one draft from local, two from cloud. Compare. If local wins often enough, trust it more.

### 4B: LoRA Fine-Tuning (Week 3-6)

The ML training doc (`docs/ML-TRAINING.md`) covers the approach. Key milestones:

- **Training data pipeline** — extract (message, model, feedback, ranking) tuples from interactions
- **Preprocessing** — deduplicate, format for DPO, balance across categories
- **QLoRA on Jetson** — use unsloth or axolotl, train during off-hours (3am cron)
- **Candidate evaluation** — before deploying a new LoRA, run it against held-out test set
- **A/B deployment** — route 10% of traffic to new model, compare feedback scores
- **Rollback** — versioned checkpoints, instant revert

The output: a local model fine-tuned on YOUR preferences. It's not a general model trying to be everything — it's YOUR model optimized for YOUR patterns.

### 4C: Semantic Caching (Week 4-5)

- **Embed similar queries** — store embeddings of past messages
- **Cache hit → return cached response** — instant, free, private
- **Cache warming** — proactively generate responses for common patterns
- **Cache invalidation** — time-based + feedback-based (👎 invalidates cache for that query cluster)

---

## Phase 5: Agent Infrastructure

**Goal:** LOG-mcp becomes the platform other agents build on.

### 5A: Task-Optimized Routing (Month 2-3)

Different tasks need different model configs. LOG-mcp learns this:

- **Task taxonomy** — auto-classify messages into categories: code_gen, summarization, brainstorm, analysis, creative_writing, q_a, debugging, planning
- **Per-task model selection** — after 100+ interactions per category, know which model/config wins
- **Auto-profile creation** — system creates custom profiles optimized for each task type
- **API** — `POST /v1/chat/completions` with `task_type` hint gets automatic best-config selection

### 5B: Multi-Agent Orchestration (Month 3-4)

LOG-mcp already does parallel draft calls. Extend to:

- **Agent dispatch** — route subtasks to different model configs:
  - Planning → reasoner model
  - Implementation → code-optimized config
  - Review → strict/precise config
- **Result merging** — take outputs from multiple agents, draft-round style, rank, merge
- **Conversation memory** — LOG-mcp manages context across agent turns, not the agents
- **API for agents** — `POST /v1/agent/task` with structured input/output

### 5C: The Marketplace (Month 4+)

- **Profile sharing** — publish your optimized profiles
- **Routing rules sharing** — "my code detection patterns"
- **Preference packs** — curated setups for specific domains
- **Metrics** — profile download count, win rate stats, user reviews

---

## The Moat

Why can't OpenAI/Anthropic/Google build this?

1. **They're vertically integrated** — they want you on THEIR model. A gateway that says "sometimes the cheap one is better" undermines their pricing.
2. **They don't have cross-provider data** — OpenAI can't optimize routing to Claude. We can.
3. **Local-first is anti-cloud** — their business model requires sending data to them.
4. **Feedback loops need time** — you need weeks of user data. New entrants start from zero.

What we have that nobody else does: **actual comparative data about which model/config is best for which task, collected from real usage, used to continuously improve routing.** That's the dataset. That's the moat.

---

## Immediate Next Steps (This Week)

1. ✅ Merge Phase 2 to main — done
2. ✅ Draft round backend + UI — done
3. ✅ Provider profiles CRUD — done
4. ✅ Settings panel redesign — done
5. ✅ E2E Docker test — done (7/8 endpoints, draft fix pushed)
6. 🔜 Re-run Docker E2E to confirm draft fix
7. 🔜 Build 3A: stats collector + routing script generator
8. 🔜 Build 3B: compare API + SDK
9. 🔜 Test on real Jetson with browser
10. 🔜 Set up Ollama on Jetson, integrate with health-aware routing

## Non-Goals (Explicitly)

- We are NOT building another ChatGPT wrapper
- We are NOT building a fine-tuning platform as a service
- We are NOT building an AI safety tool (privacy is architectural, not the product)
- We are NOT competing with LangChain/CrewAI (we're complementary — they orchestrate, we optimize model selection)
- We are NOT going to raise VC money (the moat is the data, not the code)

## Success Metrics

- **Week 1:** Stats collector running, routing updates happening automatically
- **Month 1:** Compare API used by 3+ external developers (or agents)
- **Month 2:** Local LoRA model beating cloud model on personal queries 60%+ of the time
- **Month 3:** Someone builds something on top of LOG-mcp that we didn't envision
