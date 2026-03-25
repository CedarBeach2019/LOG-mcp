"""Model discovery — fetch, filter, and search available models from OpenRouter.

Caches model lists locally to avoid repeated API calls. Designed for
resource-constrained devices (Jetson 8GB).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("vault.model_discovery")

# OpenRouter API
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
DEFAULT_CACHE_TTL = 3600  # 1 hour

CAPABILITY_TAGS = {
    "chat": ["chat", "instruct", "assistant", "conversation"],
    "code": ["code", "coding", "programmer", "developer"],
    "vision": ["vision", "image", "multimodal"],
    "reasoning": ["reasoner", "reasoning", "think", "chain-of-thought"],
}


@dataclass
class ModelInfo:
    """Parsed model metadata from OpenRouter."""
    id: str
    name: str
    context_length: int = 4096
    prompt_price_per_mtok: float = 0.0
    completion_price_per_mtok: float = 0.0
    capabilities: list[str] = field(default_factory=list)
    provider: str = ""
    description: str = ""
    raw: dict = field(default_factory=dict)

    @property
    def is_free(self) -> bool:
        return self.prompt_price_per_mtok == 0 and self.completion_price_per_mtok == 0

    def matches_capability(self, cap: str) -> bool:
        if cap == "any":
            return True
        tags = CAPABILITY_TAGS.get(cap, [cap])
        name_lower = (self.name + " " + self.description).lower()
        return any(t.lower() in name_lower for t in tags)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "context_length": self.context_length,
            "prompt_price_per_mtok": self.prompt_price_per_mtok,
            "completion_price_per_mtok": self.completion_price_per_mtok,
            "is_free": self.is_free,
            "capabilities": self.capabilities,
            "provider": self.provider,
        }


class ModelDiscovery:
    """Fetch and query the model registry from OpenRouter."""

    def __init__(self, cache_dir: Path | str | None = None, cache_ttl: int = DEFAULT_CACHE_TTL):
        self.cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".log" / "vault"
        self.cache_ttl = cache_ttl
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_file = self.cache_dir / "model_registry.json"
        self._models: list[ModelInfo] = []
        self._last_fetch = 0.0

    async def fetch_models(self, client: httpx.AsyncClient | None = None) -> list[ModelInfo]:
        """Fetch models from OpenRouter API (or use cache)."""
        # Check cache
        if self._models and (time.time() - self._last_fetch) < self.cache_ttl:
            return self._models

        # Try file cache
        if self._cache_file.exists():
            try:
                data = json.loads(self._cache_file.read_text())
                if time.time() - data.get("fetched_at", 0) < self.cache_ttl:
                    self._models = [self._parse_model(m) for m in data.get("models", [])]
                    self._last_fetch = data.get("fetched_at", 0)
                    logger.info("Loaded %d models from file cache", len(self._models))
                    return self._models
            except Exception as exc:
                logger.warning("Cache read failed: %s", exc)

        # Fetch from API
        try:
            c = client or httpx.AsyncClient(timeout=30.0)
            close_client = client is None
            try:
                resp = await c.get(OPENROUTER_MODELS_URL)
                resp.raise_for_status()
                data = resp.json()
                raw_models = data.get("data", [])
                self._models = [self._parse_model(m) for m in raw_models]

                # Save cache
                cache_data = {"fetched_at": time.time(), "models": raw_models}
                self._cache_file.write_text(json.dumps(cache_data, ensure_ascii=False))
                self._last_fetch = time.time()
                logger.info("Fetched %d models from OpenRouter API", len(self._models))
                return self._models
            finally:
                if close_client:
                    await c.aclose()
        except Exception as exc:
            logger.error("Failed to fetch models: %s", exc)
            # Return whatever we have (possibly stale cache or empty)
            return self._models

    def load_from_file(self, path: Path) -> list[ModelInfo]:
        """Load models from a local JSON file (for testing/offline)."""
        data = json.loads(path.read_text())
        models = data if isinstance(data, list) else data.get("data", data.get("models", []))
        self._models = [self._parse_model(m) for m in models]
        self._last_fetch = time.time()
        return self._models

    def _parse_model(self, raw: dict) -> ModelInfo:
        """Parse raw OpenRouter API response into ModelInfo."""
        pricing = raw.get("pricing", {})
        # OpenRouter pricing is per-token, convert to per-million
        prompt_price = float(pricing.get("prompt", "0")) * 1_000_000
        completion_price = float(pricing.get("completion", "0")) * 1_000_000

        # Detect capabilities
        name = raw.get("name", raw.get("id", ""))
        caps = []
        for cap, tags in CAPABILITY_TAGS.items():
            name_lower = (name + " " + raw.get("description", "")).lower()
            if any(t.lower() in name_lower for t in tags):
                caps.append(cap)
        if not caps:
            caps.append("chat")  # default

        return ModelInfo(
            id=raw.get("id", ""),
            name=name,
            context_length=raw.get("context_length", 4096),
            prompt_price_per_mtok=round(prompt_price, 4),
            completion_price_per_mtok=round(completion_price, 4),
            capabilities=caps,
            provider=raw.get("top_provider", {}).get("name", ""),
            description=raw.get("description", ""),
            raw=raw,
        )

    def search(
        self,
        query: str = "",
        capability: str = "any",
        max_prompt_price: float | None = None,
        min_context: int = 0,
        max_context: int | None = None,
        limit: int = 50,
    ) -> list[ModelInfo]:
        """Search/filter models from the loaded registry."""
        results = []
        query_lower = query.lower().strip()

        for m in self._models:
            if not m.matches_capability(capability):
                continue
            if max_prompt_price is not None and m.prompt_price_per_mtok > max_prompt_price:
                continue
            if m.context_length < min_context:
                continue
            if max_context is not None and m.context_length > max_context:
                continue
            if query_lower:
                searchable = (m.id + " " + m.name + " " + m.description).lower()
                if query_lower not in searchable:
                    continue
            results.append(m)
            if len(results) >= limit:
                break

        return results

    def get_model(self, model_id: str) -> ModelInfo | None:
        """Get a model by ID."""
        for m in self._models:
            if m.id == model_id:
                return m
        return None

    def list_models(self) -> list[ModelInfo]:
        """Return all loaded models."""
        return list(self._models)
