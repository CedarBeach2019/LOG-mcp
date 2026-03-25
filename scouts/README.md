# scouts/

Model evaluation agents that can independently test and score AI model responses.

## What They Do

Scouts are autonomous agents that evaluate model quality by running test prompts, scoring responses, and reporting metrics. They're used for comparative model evaluation (e.g., "Is DeepSeek-chat better than DeepSeek-reasoner for coding tasks?").

## Files

| File | Purpose |
|------|---------|
| `base.py` | **BaseScout** — abstract base class defining the scout interface |
| `claude.py` | Claude-specific scout (Anthropic API) |
| `deepseek_scout.py` | DeepSeek-specific scout (DeepSeek API) |
| `__init__.py` | Package exports |

## Scout Interface

```python
class BaseScout:
    async def evaluate(prompt: str, model: str) -> ScoutResult
    async def compare(prompt: str, models: list[str]) -> ComparisonResult
    def report() -> dict  # metrics summary
```

## How It Works

1. Scout receives a test prompt (from a predefined suite or custom)
2. Sends to target model(s) via their respective APIs
3. Scores response quality (relevance, accuracy, coherence)
4. Stores results for later analysis
5. Can compare multiple models on the same prompt

## Extending

Create a new scout by subclassing `BaseScout`:

```python
class MyScout(BaseScout):
    name = "my-scout"
    async def evaluate(self, prompt, model):
        # Call your model API
        # Score the response
        return ScoutResult(...)
```
