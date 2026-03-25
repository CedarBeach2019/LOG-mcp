# Provider System

Pluggable multi-provider abstraction for LOG-mcp.

## How It Works

Each provider is a `ProviderConfig` with:
- **name** — unique identifier (e.g., `deepseek`, `groq`)
- **base_url** — OpenAI-compatible API endpoint
- **auth_header/auth_prefix** — how to send the API key
- **models** — dict of model names → capabilities and tier
- **capabilities** — what the provider supports (chat, streaming, vision, etc.)
- **rate_limit_rpm/rate_limit_tpm** — provider rate limits
- **pricing** — cost per 1M tokens

The `ProviderRegistry` manages all providers and builds failover chains.

## Built-in Providers

| Provider | Default Models | Tier |
|----------|---------------|------|
| deepseek | deepseek-chat, deepseek-reasoner | cheap/escalation |
| groq | llama-3.3-70b-versatile, deepseek-r1-distill-llama-70b | cheap/escalation |
| openai | gpt-4o-mini, gpt-4o | cheap/escalation |
| openrouter | openai/gpt-4o-mini, anthropic/claude-3.5-sonnet | cheap/escalation |
| local | local (llama.cpp) | cheap |

## Adding a New Provider

### 1. Register in code (built-in):

Edit `vault/providers/__init__.py`, add to `BUILTIN_PROVIDERS`:

```python
"myprovider": {
    "name": "myprovider",
    "display_name": "My Provider",
    "base_url": "https://api.myprovider.com/v1/chat/completions",
    "models_url": "https://api.myprovider.com/v1/models",
    "auth_header": "Authorization",
    "auth_prefix": "Bearer",
    "models": {
        "my-model-v1": {"tier": "cheap", "streaming": True, "context": 32000},
    },
    "capabilities": ["chat", "streaming"],
    "rate_limit_rpm": 60,
    "rate_limit_tpm": 100000,
    "pricing": {"input_per_1m": 0.5, "output_per_1m": 1.5},
},
```

### 2. Register at runtime (custom):

```python
from vault.providers import ProviderRegistry, ProviderConfig, get_registry

registry = get_registry()
custom = ProviderConfig(
    name="my-custom",
    base_url="http://localhost:1234/v1/chat/completions",
    auth_header="Authorization",
    auth_prefix="Bearer",
    api_key="my-key",
    models={"local-llm": {"tier": "cheap", "streaming": True, "context": 4096}},
    capabilities=["chat", "streaming"],
)
registry.register(custom)
```

### 3. Configure API keys:

```python
registry = get_registry()
registry.update_api_key("deepseek", os.environ["DEEPSEEK_API_KEY"])
registry.update_api_key("groq", os.environ["GROQ_API_KEY"])
```

## Failover Chain

```python
registry = get_registry()
registry.update_api_key("deepseek", "sk-...")
registry.update_api_key("groq", "gsk-...")

chain = registry.get_failover_chain(["deepseek", "groq", "local"])
# → [DeepSeek, Groq, Local] — only enabled providers with keys
```

## Key Methods

| Method | Description |
|--------|-------------|
| `registry.get(name)` | Get provider by name |
| `registry.get_enabled_providers()` | All providers with API keys |
| `registry.get_failover_chain(order)` | Ordered list for failover |
| `registry.get_provider_for_model(model)` | Find provider for a model |
| `provider.get_auth_headers()` | Build auth header dict |
| `provider.get_tier_model("cheap")` | Get model by tier |
| `provider.supports("streaming")` | Check capability |
