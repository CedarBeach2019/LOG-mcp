# Dataset Quality Research Journey

## Phase 1: Codebase Analysis

### Current Pipeline (vault/training_pipeline.py)
- `extract_ranking_data()`: Queries interactions with route_action='draft' AND feedback IS NOT NULL
- `export_for_lora()`: JSONL with instruction/input/output format (Alpaca-style)
- `export_for_dpo()`: prompt/chosen/rejected pairs
- `filter_quality()`: Basic length filters (min response 50 chars, min input 5 chars)
- `deduplicate()`: Lowercase stripped user input as key, keeps most recent
- **Gap**: No quality scoring, no domain classification, no versioning, no stats

### Interactions Table Schema
- Fields: id, session_id, user_input, rewritten_input, route_action, route_reason, target_model, response, escalation_response, response_latency_ms, escalation_latency_ms, feedback, critique, created_at
- **Gap**: No ranking_time field — can't measure instant clicks. Draft interactions store individual responses per profile, not grouped winner/loser pairs in a single row.

### Draft & Feedback Flow
- `/v1/drafts` fires parallel calls, stores each as separate interaction with route_action='DRAFT'
- `/v1/elaborate` stores winner info in critique field of original interaction_id as JSON
- `/v1/feedback` stores thumbs up/down + optional critique
- **Key insight**: Winner/loser data is in the critique JSON blob, not structured columns

### Existing Quality Gaps
1. No quality scoring — all rankings treated equally
2. No low-effort detection (instant clicks, no reasoning)
3. No domain classification for diversity sampling
4. No versioned datasets
5. No readiness metrics
6. DPO export won't work well because loser_responses are always empty (data model issue)

## Phase 1: External Research

### Instruction-Tuning Dataset Quality
- Alpaca format (instruction/input/output) is standard for LoRA
- ShareGPT/OpenAssistant use conversation turns — overkill for our use case
- Key quality metrics: instruction diversity, response quality, lack of duplication
- Minimum viable for LoRA: 100-500 examples for noticeable improvement on 2B model

### DPO Best Practices
- Good pairs: clear preference signal, similar length chosen/rejected, user reasoning provided
- Bad pairs: trivially different responses, automated/random rankings, no real preference
- Minimum: 200+ ranked pairs for meaningful DPO improvement
- Reference: https://arxiv.org/abs/2305.18290 (original DPO paper)

### Low-Effort Ranking Detection
- Time-based: <2s between draft display and ranking = likely random
- Reasoning-based: No critique text = weaker signal
- Length-based: Very short responses (<20 chars) are low quality
- Consistency-based: User always picks first option = pattern, not preference

### Deduplication Strategies
- Normalize prompts: strip whitespace, lowercase, remove PII tokens
- Semantic dedup: cosine similarity on embeddings (expensive, defer)
- Near-dedup: min-hash or n-gram overlap (moderate complexity)

## Quality Scoring Rubric Design

Each ranking gets a composite score 0.0-1.0 from four dimensions:

1. **reasoning_provided** (0-0.3): User gave critique/reasoning text
2. **response_length** (0-0.2): Winner response is substantial (50+ chars = 0.1, 200+ = 0.2)
3. **uniqueness** (0-0.3): Prompt isn't a duplicate of other prompts
4. **feedback_consistency** (0-0.2): Winner was thumbs-up, losers were thumbs-down

Flags: low_effort if composite < 0.3 OR reasoning_provided == 0 AND response_length < 0.1

## Deduplication Strategy
1. Canonicalize: strip, lowercase, replace PII tokens with placeholders, collapse whitespace
2. Exact dedup: same canonical prompt → keep highest quality score
3. Near-dedup: n-gram Jaccard similarity > 0.8 → flag for review

## Proposed Improvements
1. QualityScore class with explainable dimensions
2. Domain classifier using keyword heuristics (code, writing, math, creative, factual)
3. DatasetManager with versioning, split generation, diversity sampling
4. DatasetStats with coverage analysis and readiness assessment
5. API endpoints for scoring, stats, export with filters, deduplication
