# llama.cpp Integration Research for LOG-mcp

**Target:** Jetson Super Orin Nano 8GB (ARM64, 2TB NVMe)  
**Date:** 2026-03-25  
**Context:** Deep integration into a Python LLM gateway

---

## 1. llama-cpp-python: Python Bindings

### 1.1 llama-cpp-python vs Ollama

| Aspect | llama-cpp-python | Ollama |
|--------|------------------|--------|
| **Latency** | Zero IPC overhead (ctypes/CFFI in-process) | HTTP API adds ~5-20ms per request |
| **Integration** | Direct Python API, no subprocess | REST API (OpenAI-compatible) |
| **Model mgmt** | Manual (you manage model paths) | Automatic (model library) |
| **Memory control** | Full control over GPU layers, context, KV cache | Abstracted away |
| **LoRA** | Native support via `set_lora_adapter()` | Supported |
| **Startup** | Load model once in Python process | Ollama daemon always running |
| **Async** | Native async API (`create_completion` with callbacks) | HTTP streaming |
| **Multi-model** | Load/unload in same process (with caveats) | Automatic unload with memory pressure |

**Recommendation for LOG-mcp:** Use **llama-cpp-python** as the primary inference backend for zero-overhead local inference. Keep Ollama as a fallback/alternative if you want a daemon approach for simpler model management.

### 1.2 LoRA Adapters

- **Yes**, llama-cpp-python supports LoRA natively via `llama_set_lora_adapter()`
- You can load a base model and apply multiple LoRA adapters dynamically
- Example: `model.set_lora_adapter(path, scale=1.0)` / `model.remove_lora_adapter(path)`
- **Limitation:** You cannot load two base models simultaneously in the same `Llama` instance. You need separate `Llama` objects, but they share GPU memory.
- For LOG-mcp: Load base model once, swap LoRA adapters for different tasks (summarization, classification, etc.)

### 1.3 Memory Footprint for 2B Parameter Models

| Quantization | Model Size | RAM+VRAM (approx) | Quality |
|-------------|-----------|-------------------|---------|
| Q4_K_M | ~1.2 GB | ~1.5-2 GB | Good for most tasks |
| Q5_K_M | ~1.5 GB | ~1.8-2.5 GB | Slightly better |
| Q8_0 | ~2.1 GB | ~2.5-3 GB | Near-fp16 quality |

**Context memory** (KV cache) is additive: ~0.5-2 GB depending on context length (n_ctx). For n_ctx=4096, expect ~1-1.5 GB additional.

**On 8GB Jetson:** With Q4_K_M + 4096 context, you have ~4-5 GB free for OS, other processes, or a second model. Q5_K_M is a sweet spot.

### 1.4 ARM64 Jetson: Tensor Cores & CUDA

- **Yes, llama.cpp supports CUDA on Jetson.** Build with `-DGGML_CUDA=ON`
- Jetson Orin uses Ampere architecture (sm_87). llama.cpp will use CUDA cores and Tensor Cores automatically for supported operations
- **Key CMAKE flags for Jetson:**
  ```bash
  CMAKE_ARGS="-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=87" pip install llama-cpp-python
  ```
- `n_gpu_layers=-1` loads all layers to GPU (recommended on Orin with 8GB shared memory)
- Jetson uses **unified memory** — CPU and GPU share the same 8GB RAM. This is an advantage (no data copies) but means everything competes for the same pool
- **Jetson-specific tip:** Set `export CUDA_VISIBLE_DEVICES=0` and consider `jetson_clocks --max` to lock max GPU clocks

### 1.5 Hot-Swapping Models

- Load time for 2B Q4 model from NVMe: **~1-3 seconds** (memory-mapped, very fast)
- Unload is instant (memory free)
- With mmap, the model file is lazily loaded from NVMe, so subsequent loads of the same model are faster (OS page cache)
- **For LOG-mcp:** Keep a "primary" model warm (always loaded), hot-swap secondary models on demand. Load time is fast enough for interactive use.

---

## 2. llama-server (Server Mode)

### 2.1 OpenAI-Compatible API

llama.cpp ships with `llama-server` (built from `examples/server`). llama-cpp-python also ships its own OpenAI-compatible server.

**Endpoints:**
- `POST /v1/chat/completions` — chat with streaming
- `POST /v1/completions` — raw completion
- `POST /v1/embeddings` — **yes, embedding support** (critical for semantic caching!)
- `POST /v1/rerank` — reranking endpoint
- `GET /v1/models` — list loaded models

### 2.2 Running Alongside Ollama

- **Yes, they can coexist** but they share the same GPU VRAM
- Default ports: Ollama=11434, llama-server=8080
- Risk: if both load models to GPU, they'll OOM on 8GB
- **Strategy:** Use one at a time, or run llama-server on CPU-only for a lightweight secondary model

### 2.3 Concurrent Requests & Streaming

- **Concurrent requests:** llama.cpp server handles multiple connections but processes them **sequentially** within a single model instance. Requests are queued.
- For true parallelism, you'd need multiple server instances with different models
- **Streaming:** Full SSE streaming support, same as OpenAI API. `stream: true` in request body

### 2.4 Embedding Endpoint

- **Critical for LOG-mcp semantic caching.** The `/v1/embeddings` endpoint is production-ready
- Uses the same model (any GGUF model can produce embeddings)
- For dedicated embedding, consider a small model like `nomic-embed-text` (Q4_K_M ~120MB) on CPU while your LLM runs on GPU

---

## 3. Performance Tricks

### 3.1 Prompt Caching (KV Cache)

- **Yes**, llama.cpp has prompt caching via **prefix caching** (since ~2024)
- Set `--cache-prompt` (server) or `cache_prompt=True` (Python API)
- Caches KV tensors for prompt prefixes so common system prompts aren't recomputed
- **Huge win for LOG-mcp:** If every request shares a system prompt, prompt eval drops from ~seconds to ~milliseconds
- For chat sessions, the conversation history prefix is cached automatically

### 3.2 Batched Inference

- `llama_decode()` supports batched decoding (multiple sequences at once)
- For parallel draft calls (speculative decoding): llama.cpp has built-in **speculative decoding** support
- `n_parallel` parameter allows processing multiple prompts simultaneously (not interleaved generation, but batched prompt eval)
- **BOS + batch size > 1** for prompt processing can give 2-4x speedup on prompt eval

### 3.3 Memory-Mapped Models from NVMe

- **Default behavior:** llama.cpp uses mmap to load GGUF files. The OS handles paging.
- With 2TB NVMe, this is essentially free — model files are lazily loaded
- First inference of a new model may have slight latency from page faults; subsequent runs are warm
- **No need to copy models to RAM first.** The mmap approach is optimal for NVMe

### 3.4 Quantization Comparison for 2B on 8GB ARM

| Quant | Size | Prompt Speed | Generation Quality | Recommendation |
|-------|------|-------------|-------------------|----------------|
| Q3_K_M | ~0.9 GB | Fastest | Noticeable degradation | Only if desperate |
| Q4_K_M | ~1.2 GB | Fast | Good | **Best default** |
| Q5_K_M | ~1.5 GB | Fast | Very good | **Sweet spot** |
| Q8_0 | ~2.1 GB | Slightly slower | Near-perfect | If memory allows |
| F16 | ~4.0 GB | Slower | Reference | Not worth it vs Q8 |

**On Jetson 8GB unified memory:** Q4_K_M or Q5_K_M are optimal. Leaves room for KV cache, OS, and other processes.

### 3.5 Keeping Model Warm in GPU Memory

- `n_gpu_layers=-1` keeps the entire model on GPU. As long as the `Llama` Python object is alive, the model stays loaded
- **llama-cpp-python pattern:** Create the model once at startup, keep the reference alive in your gateway class
- llama.cpp server (`llama-server`) keeps models loaded until explicitly unloaded or OOM
- On Jetson unified memory, "GPU memory" = "system memory" — no separate allocation to worry about

---

## 4. Integration Patterns

### 4.1 Direct Ctypes/CFFI (Zero Overhead)

llama-cpp-python already uses ctypes to call llama.cpp's C API. This is already zero-overhead (no subprocess, no HTTP).

```python
from llama_cpp import Llama

# Load once at startup
model = Llama(
    model_path="/nvme/models/phi-3-mini-4k-q4_k_m.gguf",
    n_gpu_layers=-1,       # All layers on GPU
    n_ctx=4096,
    cache_prompt=True,     # KV cache across calls
    verbose=False,
)

# Inference is in-process, no HTTP overhead
response = model.create_chat_completion(
    messages=[{"role": "user", "content": "Hello"}],
    stream=True,
    max_tokens=256,
)
```

**This is the recommended approach for LOG-mcp.** No need to go lower than llama-cpp-python.

### 4.2 Async Patterns

```python
# Streaming async generator
import asyncio
from llama_cpp import Llama

model = Llama(...)

async def stream_completion(prompt: str):
    """Stream tokens asynchronously."""
    for chunk in model.create_completion(prompt, stream=True, max_tokens=256):
        token = chunk["choices"][0]["text"]
        yield token
        await asyncio.sleep(0)  # Yield to event loop

# Use with asyncio
async for token in stream_completion("Tell me a joke"):
    print(token, end="")
```

llama-cpp-python's `stream=True` returns a generator. Wrap in an async generator with `asyncio.sleep(0)` yields to keep the event loop responsive. The actual inference runs on a thread internally.

**For a FastAPI/asyncio gateway**, use this pattern. The inference itself is blocking (C call) but you can run it in a thread pool executor if needed.

### 4.3 Multiple Models on Same GPU

- **Challenge:** All models share the same 8GB unified memory on Jetson
- **Strategy 1 — Single primary model:** Keep one model hot, cold-load others on demand (1-3s load time from NVMe)
- **Strategy 2 — Split layers:** Load primary model fully on GPU, secondary partially on GPU (`n_gpu_layers=10` for a 2B model)
- **Strategy 3 — Dedicated embedding model on CPU:** Run a tiny embedding model (nomic-embed-text Q4, ~120MB) on CPU cores, full LLM on GPU

---

## 5. Projects Worth Studying

### 5.1 text-generation-webui (oobabooga)
- **GitHub:** oobabooga/text-generation-webui
- Uses llama.cpp as one of many backends
- Good reference for multi-backend abstraction, model management UI
- Shows how to handle model switching, LoRA loading, parameter management

### 5.2 Koboldcpp
- **GitHub:** kaiokendev/koboldcpp
- llama.cpp-based server focused on creative writing
- Good reference for: efficient memory management, session-based KV cache, softprompt management
- Shows practical ARM/edge deployment patterns

### 5.3 TabbyAPI
- **GitHub:** theroyallab/tabbyAPI
- **Highly relevant.** FastAPI-based LLM server built on llama.cpp
- OpenAI-compatible, supports multiple models, LoRA hot-swapping, embedding endpoints
- Shows the exact architecture LOG-mcp should consider:
  - Multi-model management with GPU memory tracking
  - Model priority queues
  - Health endpoints for monitoring

### 5.4 llama.cpp + Cloud Fallback Patterns
- **LiteLLM** (`gh/berriai/litellm`): Proxy that routes to OpenAI/Anthropic/local with unified API. Supports llama.cpp as a backend. **This is exactly the "gateway with cloud fallback" pattern.**
- **Simple one:** Keep `litellm` as the routing layer, configure llama.cpp as the "local" provider, OpenAI/Anthropic as fallback

---

## 6. Concrete Recommendations for LOG-mcp

### Architecture

```
┌─────────────────────────────────────────────┐
│               LOG-mcp Gateway                │
│              (FastAPI / asyncio)             │
├─────────────────────────────────────────────┤
│  Request Router                              │
│  ├── Semantic similarity check (embeddings)  │
│  ├── Prompt cache hit? → Return cached       │
│  └── Route to provider                       │
├─────────────────────────────────────────────┤
│  Providers                                   │
│  ├── llama-cpp-python (in-process, primary)  │
│  │   ├── Primary model (warm, Q5_K_M)       │
│  │   ├── LoRA swap for task adaptation       │
│  │   └── KV prompt caching enabled           │
│  ├── Ollama (secondary, for model variety)   │
│  └── Cloud fallback (OpenAI/Anthropic)       │
├─────────────────────────────────────────────┤
│  Embedding Service (separate model on CPU)   │
│  └── nomic-embed-text Q4 (~120MB)           │
└─────────────────────────────────────────────┘
```

### Build Commands (Jetson)

```bash
# llama-cpp-python with CUDA for Jetson Orin
CMAKE_ARGS="-DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=87" \
  pip install llama-cpp-python

# Verify CUDA is working
python -c "from llama_cpp import Llama; print('OK')"
```

### Recommended Models for Jetson 8GB

| Purpose | Model | Quant | Size | Where |
|---------|-------|-------|------|-------|
| Primary LLM | Phi-3-mini-4k, Gemma-2-2b, Qwen2.5-1.5b | Q5_K_M | ~1.5 GB | GPU |
| Fast LLM | TinyLlama-1.1B | Q4_K_M | ~0.7 GB | GPU or CPU |
| Embeddings | nomic-embed-text | Q4_K_M | ~120 MB | CPU |
| Classification | Same as primary + LoRA | — | — | GPU |

### Key Configuration

```python
PRIMARY_MODEL = Llama(
    model_path="/nvme/models/primary-q5_k_m.gguf",
    n_gpu_layers=-1,
    n_ctx=4096,
    cache_prompt=True,
    n_batch=512,           # Larger batch for prompt eval
    n_threads=2,           # Leave CPU threads for embedding model
    use_mlock=True,        # Prevent swapping on constrained memory
    verbose=False,
)
```

### Priority Matrix

1. **Start with llama-cpp-python in-process** (not Ollama, not llama-server) — lowest latency, most control
2. **Enable prompt caching** — essential for repeated system prompts in LOG-mcp
3. **Separate embedding model on CPU** — don't waste GPU memory on embeddings
4. **Use LiteLLM for cloud fallback** — proven proxy with unified API
5. **NVMe mmap** is default and optimal — don't overthink storage
6. **Q5_K_M** for primary model — best quality/size tradeoff on 8GB
7. **Monitor GPU memory** — on unified Jetson memory, LOG-mcp itself + other processes compete with the model

### Gotchas for Jetson

- Jetson Power Mode: Run `jetson_clocks --max` and set power mode to MAXN for consistent performance
- Thermal throttling is real — sustained inference will heat up. Monitor with `tegrastats`
- CUDA version on Jetson is typically 12.2 (JetPack 6.x) — build llama-cpp-python against that
- Shared memory means the model competes with everything else. Budget carefully.
- `n_gpu_layers=-1` is correct for Jetson (no separate VRAM to manage)
