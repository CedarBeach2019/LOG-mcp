# Jetson GPU Inference Notes

## Hardware: Jetson Super Orin Nano 8GB

### Memory Reality
- 8GB shared GPU/CPU (unified memory architecture)
- Typical free RAM: 2-3GB (after OS + services)
- llama.cpp model + KV cache needs contiguous GPU memory
- **Rule of thumb: model must be < 1.5GB with KV cache room**

### Model Sizes vs GPU Layers
| Model | Quant | Size | Max GPU Layers |
|-------|-------|------|----------------|
| Qwen2.5-1.5B | Q4_K_M | 202MB | 5 (with other services) |
| Qwen2.5-1.5B | Q5_K_M | 1070MB | 20 (bare metal), 0 (with services) |
| Qwen2.5-1.5B | Q8_0 | 1530MB | 0 (CPU only) |

### Performance (Qwen2.5-1.5B Q5_K_M)
| Config | Load Time | Inference (8 tok) | tok/s |
|--------|-----------|--------------------|----|
| CPU only | 1.5s | 0.92s | ~8.7 |
| 5 GPU layers | N/A | 0.55s | ~14.5 |
| 20 GPU layers | 1.1s | 0.70s | ~11.4 |

### Key Findings
1. **GPU inference is faster** even with 5 layers — mixed CPU/GPU beats pure CPU
2. **Default should be 5 GPU layers** — works in all conditions
3. **The server process itself uses ~500MB** — reduces available GPU memory
4. **Q4_K_M is the sweet spot** — 202MB leaves room for KV cache and other processes
5. **n_ctx matters** — reducing from 4096 to 1024 saves ~200MB of KV cache memory
6. **Embedding model should be CPU-only** (sentence-transformers) — BERT GGUF not supported by llama-cpp-python

### Embeddings
- **sentence-transformers** all-MiniLM-L6-v2: 384 dims, 0.08s/batch, works perfectly
- llama-cpp-python `embed()` doesn't support BERT architecture models
- Embeddings should run on CPU (model is tiny, ~90MB)

### Recommendations
1. Use Q4_K_M for production (best size/performance ratio)
2. Default `gpu_layers=5`, let user increase if RAM is available
3. `n_ctx=2048` default (not 4096 — saves GPU memory)
4. Load model lazily on first local inference request
5. Auto-detect available memory and adjust layers accordingly
6. Consider TinyLlama 1.1B Q4 for always-on local inference (~700MB total with cache)
