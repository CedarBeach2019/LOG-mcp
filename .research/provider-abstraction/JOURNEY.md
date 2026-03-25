# Provider Abstraction — Research & Implementation Journey

## Phase 1: Codebase Study

### Current Architecture
- **call_model()** in `gateway/shared.py`: Simple function taking `(endpoint, api_key, model, messages, ...)`. Always uses `Authorization: Bearer` header. No concept of "provider" — just raw URLs.
- **resilient_call()** in `gateway/error_boundary.py`: Hardcoded 2-tier failover: cheap ↔ escalation (DeepSeek). Swaps which is primary based on which endpoint you pass. No multi-provider chain.
- **VaultSettings** in `vault/config.py`: Flat settings with `cheap_model_endpoint`, `escalation_model_endpoint`, single `api_key`. All assume DeepSeek by default.
- **routes.py**: Calls `call_model()` directly with settings endpoints. Draft profiles can specify custom endpoints but still Bearer auth only.

### Critiques
1. **Single API key for all providers** — can't use different keys for OpenAI vs Groq vs DeepSeek
2. **No provider identity** — just URLs. Changing provider means changing URL + hoping auth works
3. **Hardcoded 2-tier failover** — can't do DeepSeek → Groq → Local → error
4. **No per-provider rate limiting awareness** — just retries blindly
5. **No capability awareness** — some models don't support streaming, function calling, etc.

### External Research Findings
- **LiteLLM** approach: `litellm.completion(model="deepseek/deepseek-chat", ...)` — uses `provider/model` prefix convention. Maintains a mapping of base URLs and auth per provider.
- **OpenAI-compatible spec**: Most providers follow `/v1/chat/completions` with `{model, messages, temperature, stream}`. Auth is always `Bearer` token. The main differences are base URL and token format.
- **Provider differences**: Some use different header names (Anthropic uses `x-api-key`), but most OpenAI-compatible providers use standard Bearer auth.

## Design Decisions

### Approach: Provider Registry Pattern
- Each provider is a `ProviderConfig` with: name, base_url, models, auth_header, capabilities, rate_limits
- `ProviderRegistry` holds all configured providers, supports `get_provider(name)` and `failover_chain()`
- Keep `call_model()` signature compatible but route through registry
- Failover chain configurable via settings: `LOG_PROVIDER_CHAIN=deepseek,groq,local`

### Tradeoffs
- **Chose**: Simple config-based approach over class hierarchy (too complex for what's needed)
- **Chose**: Keep OpenAI-compatible API as the universal interface (covers DeepSeek, Groq, OpenAI, OpenRouter, Together)
- **Chose**: Provider as config dict, not abstract base class — simpler, more Pythonic
- **Avoided**: LiteLLM as dependency — adds too much weight for this use case
