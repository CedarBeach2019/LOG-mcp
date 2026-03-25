# Prompt Caching Notes

## Status
Prompt caching (`cache_prompt=True` in llama-cpp-python) should provide significant speedups
when the same system prompt is reused across requests. This is important for LOG-mcp because
every chat completion includes a system prompt with user preferences.

## Expected Behavior
- First call with a system prompt: evaluates full prompt (slow)
- Subsequent calls with same system prompt: reuses KV cache (fast)
- Speedup depends on system prompt length (longer = more savings)

## Jetson CPU-Only Notes
- On CPU-only inference (no GPU layers), the speedup may be minimal because
  the KV cache lookup is not the bottleneck — the matrix multiplication is
- Prompt caching is most effective on GPU where KV cache lookup is fast

## TODO
- Benchmark on GPU (need more free RAM)
- Integrate `cache_prompt=True` into the chat_completions endpoint
- Track whether system prompt changed between requests to enable/disable caching
