# docs/

Design documents, research notes, and planning artifacts for LOG-mcp.

## Contents

| File | Description |
|------|-------------|
| `ARCHITECTURE.md` | System architecture diagram, module map, API surface, design decisions |
| `ROADMAP-v3.md` | Development roadmap with milestone tracking (12/18 complete) |
| `QUICKSTART.md` | Getting started guide — install, configure, run |
| `LLAMACPP-INTEGRATION.md` | llama-cpp-python integration research: quant comparison, prompt caching, in-process vs Ollama |
| `JETSON-INFERENCE.md` | Jetson Orin GPU constraints, memory benchmarks, model sizing recommendations |
| `PERFORMANCE-AUDIT.md` | Performance audit results — all HIGH/MEDIUM issues identified and fixed |
| `SUPERINSTANCE-ANALYSIS.md` | Analysis of similar projects (superinstance repos) — concepts worth borrowing |
| `ML-TRAINING.md` | Plans for LoRA fine-tuning and training data extraction from interactions |
| `PHASE2-PLAN.md` | Phase 2 implementation plan (completed) |
| `PHASE2-REASONER.md` | Reasoning model integration notes (completed) |
| `PROMPT-CACHING.md` | Prompt caching research and integration notes |
| `setup.sh` | One-command setup script |

## Contributing

When adding a new document:
1. Use descriptive filenames (UPPER_SNAKE_CASE.md for design docs)
2. Include a one-paragraph summary at the top
3. Add an entry to this README
4. Keep it focused — link to code rather than duplicating
