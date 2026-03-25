# ML-TRAINING.md — On-Device Training Pipeline

*Jetson Super Orin Nano (8GB VRAM, 2TB NVMe), qwen3.5:2b base model.*

---

## Feasibility Matrix

| Capability | Status | Notes |
|---|---|---|
| LoRA fine-tuning 2B on 8GB | **Definitely possible** | ~3-4GB VRAM for training, leaves room for inference |
| Inference + training simultaneously | **Definitely possible** | Pause training on inference request, resume after |
| DPO on 2B model on-device | **Probably possible** | Requires careful memory management |
| Auto-routing from preference data | **Definitely possible** | Logistic regression or small classifier |
| Draft quality predictor | **Probably possible** | Heuristics first, tiny model later |
| Online DPO (per-interaction updates) | **Speculative** | Batch training is more practical and stable |
| Catastrophic forgetting prevention | **Definitely possible** | Replay buffer + versioned checkpoints |

---

## 1. LoRA Fine-Tuning: The Basics

**Library recommendation:** [Unsloth](https://github.com/unslothai/unsloth) — 2x faster than vanilla HuggingFace, explicit Jetson/ARM support, ~3GB VRAM for 2B model training. Runner-up: `llama.cpp` with LoRA (simpler but slower, no gradient checkpointing).

**Not recommended:** Axolotl. Heavy dependency tree, designed for multi-GPU setups, overkill for our use case.

**Training data format** — Instruction tuning for response quality:

```json
{"instruction": "User message here", "output": "The winning response the user selected"}
```

**DPO format** — When we have rankings (chosen vs rejected):

```json
{"prompt": "User message", "chosen": "Winner draft", "rejected": "Loser draft"}
```

Start with instruction tuning. Switch to DPO once we have 200+ ranked pairs (see §2).

**Hardware reality:** QLoRA (4-bit base + LoRA adapters) on qwen3.5:2b uses ~3.2GB VRAM. Leaves ~4.8GB free — inference can coexist by pausing training during requests. Training time: ~15-20 minutes for 500 examples, 3 epochs on Jetson Orin Nano. One hour for 2000 examples.

**LoRA adapter size:** ~50MB per checkpoint. Keep last 5 = 250MB. Negligible on 2TB NVMe.

---

## 2. DPO: Direct Preference Optimization

DPO learns from pairwise comparisons (winner vs loser draft). It's more powerful than plain instruction tuning because it captures *relative* quality, not just "this response was good."

**Minimum data:** DPO needs at least 100 ranked pairs before you'll notice improvement. Meaningful improvement starts around 300-500 pairs. Below 100, instruction tuning on winners alone is more reliable.

**Feasibility on Jetson:** DPO requires computing loss on both chosen and rejected responses per batch, roughly doubling memory compared to instruction tuning. With QLoRA and batch_size=1 (gradient accumulation to simulate larger batches), this fits in ~4-5GB VRAM. Tight but workable — inference must pause during DPO training.

**Batch vs online:** Batch training is the right call. Accumulate data, retrain weekly during idle hours (3-6am). Online DPO (updating after every interaction) is theoretically appealing but practically fragile — single bad examples can destabilize weights, and the overhead of频繁 small training runs isn't worth it.

**Recommended cadence:** 
- Weeks 1-3: Collect data, instruction-tune winners only (if >100 positive examples).
- Weeks 4+: DPO with ranked pairs, weekly retraining.
- Always: Keep a holdout set (20% of interactions) to evaluate if the new checkpoint is actually better before swapping it in.

---

## 3. Router Classifier: Don't Overthink It

**Key insight from PHASE2-PLAN:** The routing script is rule-based (regex). The ML optimizer *updates the rules*, it doesn't *replace* them.

**Training approach:** Don't train a neural classifier. Use the stored interaction data (route_action + feedback) to:
1. Measure per-rule accuracy: for each regex pattern, what % of ESCALATE messages got thumbs-up vs thumbs-down?
2. Adjust confidence thresholds: if `r"(debug|traceback)\b"` escalates 100 messages and 90% get 👍, it's a good rule. If 40% get 👎, tighten it.
3. Add new rules from patterns in misclassified messages (cluster by user_input similarity).

This is a **stats script**, not a model. Runs in <100ms. No VRAM needed. Update the routing script's RULES dict accordingly.

**If we do want a neural router later** (after 1000+ classified interactions): Fine-tune qwen3.5:2b on the classification task with LoRA. Expected accuracy: 85-92% on a 7-class problem with 2B parameters — well-established in literature. But this is P3 priority; the stats-based approach is better for the first few months.

---

## 4. Practical Training Loop

```
Every Sunday 3am:
  1. Export interactions from last 7 days with feedback != NULL
  2. Split: 80% train, 20% eval
  3. If ranked pairs >= 200: run DPO (3 epochs, QLoRA)
     Else if positive examples >= 100: run instruction tuning (3 epochs)
     Else: skip
  4. Evaluate new adapter on holdout set (generate responses, compare)
  5. If eval_score > current_score - 0.02 (tolerance): swap adapter in
  6. If worse: discard, keep previous adapter
  7. Log everything to training_runs table
```

**Catastrophic forgetting:** Mix 20% old data (randomly sampled from all prior interactions) into each training batch. This replay buffer ensures the model doesn't "forget" what it learned from earlier users/style preferences. Keep old data capped at 2000 examples to avoid training bloat.

**Versioning:** Each checkpoint gets a timestamped filename and a row in `training_runs`:

```sql
CREATE TABLE training_runs (
    id INTEGER PRIMARY KEY,
    adapter_path TEXT,
    base_model TEXT,
    dataset_size INTEGER,
    training_type TEXT,  -- 'instruction' or 'dpo'
    eval_score REAL,
    activated_at TEXT,
    rolled_back INTEGER DEFAULT 0
);
```

Rollback: `ollama create mymodel-from-backup --from checkpoint_timestamp`. Takes 30 seconds.

---

## 5. Draft Quality Predictor

**Phase 1 (now): Heuristics.** No ML needed. Score drafts based on:
- Draft rank position (if user consistently picks draft #2, weight that model/temperature higher)
- Response length ratio (user prefers 150-300 word responses for simple queries)
- Latency (user rarely picks the slow draft unless quality demands it)

Combine into a weighted score: `0.5*pick_rate + 0.2*length_fit + 0.3*latency_preference`. After 50+ picks, this heuristic is surprisingly accurate.

**Phase 2 (200+ ranked interactions): Train a lightweight reward model.** Use the ranking data to train a small classifier (logistic regression on embeddings, or a tiny BERT if you want) that predicts `P(user picks this draft | input, draft, metadata)`. This model is ~10MB, runs in <10ms, and lets the system auto-select the best draft without showing all options.

**Phase 3 (1000+): Use the reward model to generate synthetic rankings.** When the user *doesn't* rank drafts, the reward model picks the winner. This generates training signal for free, accelerating the DPO loop.

---

## 6. What's Realistic in 90 Days

| Milestone | When | Dependency |
|---|---|---|
| Instruction tuning on thumbs-up responses | Week 3 | 100+ positive examples |
| Heuristic draft predictor | Week 2 | 50+ ranked interactions |
| Stats-based routing optimizer | Week 4 | 200+ interactions with feedback |
| DPO training loop | Week 6-8 | 200+ ranked pairs |
| Automated weekly retraining | Week 8 | Stable DPO pipeline |
| Reward model for draft prediction | Week 12 | 500+ ranked pairs |

**Don't build:** Online/incremental DPO. Separate neural router. Any model >4B on-device. Reinforcement learning from human feedback (full RLHF) — DPO is simpler and sufficient.

**Do build:** Versioned checkpoints with rollback. Holdout evaluation before swapping. Replay buffer for forgetting prevention. Training run logging so you can debug what went wrong.

**The honest truth:** On a Jetson with 8GB, you can do useful LoRA fine-tuning on a 2B model. You cannot do full fine-tuning, train large reward models, or run training and inference at full speed simultaneously. The system works within these constraints and they're not a problem — the bottleneck is *data*, not compute. You need weeks of user interactions before training matters. Design for that timeline.
