#!/usr/bin/env python3
"""Test local inference on the Jetson once llama-cpp-python is built."""

import sys
import time

def test_import():
    print("1. Testing import...")
    try:
        from llama_cpp import Llama
        print("   ✅ llama_cpp imported")
        return Llama
    except ImportError as e:
        print(f"   ❌ Import failed: {e}")
        return None

def test_load_model(Llama, model_path):
    print(f"2. Loading model: {model_path}")
    t0 = time.time()
    try:
        model = Llama(
            str(model_path),
            n_gpu_layers=-1,  # all layers on GPU
            n_ctx=2048,
            verbose=True,
        )
        elapsed = time.time() - t0
        print(f"   ✅ Model loaded in {elapsed:.1f}s")
        return model
    except Exception as e:
        print(f"   ❌ Load failed: {e}")
        return None

def test_inference(model):
    print("3. Testing inference...")
    messages = [
        {"role": "system", "content": "You are a helpful assistant. Be brief."},
        {"role": "user", "content": "What is 2+2? Reply with just the answer."},
    ]
    t0 = time.time()
    try:
        response = model.create_chat_completion(
            messages=messages,
            max_tokens=32,
            temperature=0.1,
            stream=False,
        )
        elapsed = time.time() - t0
        content = response["choices"][0]["message"]["content"]
        print(f"   ✅ Response: {content}")
        print(f"   ⏱️  Latency: {elapsed:.2f}s ({elapsed*1000:.0f}ms)")
        return content
    except Exception as e:
        print(f"   ❌ Inference failed: {e}")
        return None

def test_streaming(model):
    print("4. Testing streaming...")
    messages = [
        {"role": "user", "content": "Count to 5."},
    ]
    t0 = time.time()
    try:
        stream = model.create_chat_completion(
            messages=messages,
            max_tokens=32,
            stream=True,
        )
        chunks = []
        for chunk in stream:
            delta = chunk["choices"][0].get("delta", {}).get("content", "")
            if delta:
                chunks.append(delta)
                print(delta, end="", flush=True)
        elapsed = time.time() - t0
        print(f"\n   ✅ Streamed {len(chunks)} tokens in {elapsed:.2f}s")
        return True
    except Exception as e:
        print(f"   ❌ Streaming failed: {e}")
        return None

def test_embeddings(model):
    print("5. Testing embeddings...")
    try:
        result = model.embed("Hello, world!")
        dims = len(result)
        print(f"   ✅ Embedding: {dims} dimensions")
        return True
    except Exception as e:
        print(f"   ❌ Embeddings failed: {e}")
        return None

def test_prompt_cache(model):
    print("6. Testing prompt caching (same system prompt, different user)...")
    messages1 = [
        {"role": "system", "content": "You are a helpful coding assistant." * 10},
        {"role": "user", "content": "What is Python?"},
    ]
    messages2 = [
        {"role": "system", "content": "You are a helpful coding assistant." * 10},
        {"role": "user", "content": "What is JavaScript?"},
    ]
    
    t0 = time.time()
    model.create_chat_completion(messages=messages1, max_tokens=16, cache_prompt=True)
    first = time.time() - t0
    
    t0 = time.time()
    model.create_chat_completion(messages=messages2, max_tokens=16, cache_prompt=True)
    second = time.time() - t0
    
    speedup = first / second if second > 0 else 0
    print(f"   First: {first:.2f}s, Second (cached): {second:.2f}s")
    print(f"   ✅ Cache speedup: {speedup:.1f}x")
    return speedup

if __name__ == "__main__":
    from pathlib import Path
    model_path = Path.home() / ".log" / "models" / "qwen2.5-1.5b-instruct-q5_km.gguf"
    
    if not model_path.exists():
        print(f"❌ Model not found: {model_path}")
        sys.exit(1)
    
    print(f"Model: {model_path} ({model_path.stat().st_size / 1024 / 1024:.0f} MB)")
    print(f"{'='*50}")
    
    Llama = test_import()
    if not Llama:
        sys.exit(1)
    
    model = test_load_model(Llama, model_path)
    if not model:
        sys.exit(1)
    
    test_inference(model)
    test_streaming(model)
    test_embeddings(model)
    test_prompt_cache(model)
    
    print(f"\n{'='*50}")
    print("All tests complete!")
