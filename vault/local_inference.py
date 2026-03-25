"""Local inference backend using llama-cpp-python for in-process GPU inference."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

# Lazy import — llama-cpp-python is optional
_llama = None


def _get_llama():
    global _llama
    if _llama is None:
        try:
            from llama_cpp import Llama
            _llama = Llama
        except ImportError:
            logger.warning("llama-cpp-python not installed. Local inference disabled.")
            return None
    return _llama


class LocalInferenceBackend:
    """Manages a single llama.cpp model for in-process inference.

    Thread-safe. Lazy-loads on first generate() call.
    Embeddings use sentence-transformers (separate, CPU-based).
    """

    def __init__(self, model_path: Path, gpu_layers: int = -1, ctx_size: int = 4096):
        self.model_path = Path(model_path)
        self.gpu_layers = gpu_layers
        self.ctx_size = ctx_size
        self._model = None
        self._embed_model = None
        self._embed_model_name = "all-MiniLM-L6-v2"
        self._lock = threading.Lock()
        self._loaded_at = None

    def load(self) -> bool:
        Llama = _get_llama()
        if Llama is None:
            return False
        if self._model is not None:
            return True
        try:
            self._model = Llama(
                str(self.model_path),
                n_gpu_layers=self.gpu_layers,
                n_ctx=self.ctx_size,
                verbose=False,
                embedding=True,
            )
            self._loaded_at = time.time()
            logger.info("Loaded local model: %s (%d GPU layers)", self.model_path.name, self.gpu_layers)
            return True
        except Exception as exc:
            logger.error("Failed to load model %s: %s", self.model_path, exc)
            return False

    def unload(self) -> None:
        with self._lock:
            if self._model is not None:
                del self._model
                self._model = None
                self._last_system = None
                self._loaded_at = None
                logger.info("Unloaded local model")

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def generate(self, messages: list[dict], temperature: float = 0.7,
                 max_tokens: int = 512) -> str | None:
        """Synchronous generation. Returns text or None on failure."""
        if not self.load():
            return None
        with self._lock:
            try:
                response = self._model.create_chat_completion(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=False,
                )
                return response["choices"][0]["message"]["content"]
            except Exception as exc:
                logger.error("Local inference failed: %s", exc)
                return None

    async def agenerate(self, messages: list[dict], temperature: float = 0.7,
                        max_tokens: int = 512) -> str | None:
        """Async wrapper — runs sync generate in executor."""
        import asyncio
        return await asyncio.to_thread(self.generate, messages, temperature, max_tokens)

    async def stream(self, messages: list[dict], temperature: float = 0.7,
                     max_tokens: int = 512) -> AsyncIterator[str]:
        """Async streaming generator."""
        if not self.load():
            return
        Llama = _get_llama()
        if Llama is None:
            return
        import asyncio
        loop = asyncio.get_event_loop()
        with self._lock:
            try:
                stream = self._model.create_chat_completion(
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    stream=True,
                )
                for chunk in stream:
                    delta = chunk["choices"][0].get("delta", {}).get("content", "")
                    if delta:
                        yield delta
            except Exception as exc:
                logger.error("Local streaming failed: %s", exc)

    def embed(self, text: str) -> list[float] | None:
        """Get embeddings using sentence-transformers (CPU, 384 dims)."""
        try:
            if self._embed_model is None:
                from sentence_transformers import SentenceTransformer
                self._embed_model = SentenceTransformer(self._embed_model_name)
            import numpy as np
            emb = self._embed_model.encode(text)
            return emb.tolist()
        except ImportError:
            logger.warning("sentence-transformers not installed. Embeddings disabled.")
            return None
        except Exception as exc:
            logger.error("Embedding failed: %s", exc)
            return None

    def get_model_info(self) -> dict[str, Any]:
        """Return model metadata and resource usage."""
        info: dict[str, Any] = {
            "loaded": self.is_loaded,
            "model_path": str(self.model_path),
            "model_name": self.model_path.stem,
            "file_size_mb": round(self.model_path.stat().st_size / 1024 / 1024, 1) if self.model_path.exists() else 0,
        }
        if self._model is not None:
            try:
                info["n_ctx"] = self._model.n_ctx()
                info["n_vocab"] = self._model.n_vocab()
                loaded_at = self._loaded_at or 0
                info["uptime_seconds"] = round(time.time() - loaded_at, 0)
            except Exception:
                pass
        return info
