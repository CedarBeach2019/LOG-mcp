"""Model manager — scans for .gguf files, manages loading/unloading, auto-selects.

Supports two modes:
- in-process: llama-cpp-python loaded directly (default)
- subprocess: separate process for isolated GPU memory (use on constrained devices like Jetson)
"""

from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

from .local_inference import LocalInferenceBackend

logger = logging.getLogger(__name__)


class ModelManager:
    """Manages available local models and hot-swapping."""

    def __init__(self, models_dir: Path, gpu_layers: int = -1, ctx_size: int = 4096,
                 use_subprocess: bool = False, python: str = "python3"):
        self.models_dir = Path(models_dir)
        self.gpu_layers = gpu_layers
        self.ctx_size = ctx_size
        self.use_subprocess = use_subprocess
        self.python = python
        self._backend: LocalInferenceBackend | None = None
        self._subprocess_client = None  # SubprocessModelClient
        self._lock = threading.Lock()
        self.models_dir.mkdir(parents=True, exist_ok=True)

    def scan_models(self) -> list[dict]:
        """Scan models_dir for .gguf files and return metadata."""
        models = []
        for f in sorted(self.models_dir.glob("*.gguf")):
            size_mb = round(f.stat().st_size / 1024 / 1024, 1)
            # Extract quantization from filename
            name = f.stem
            quant = "unknown"
            for q in ["Q2_K", "Q3_K", "Q4_0", "Q4_K", "Q5_0", "Q5_K", "Q6_K", "Q8_0", "F16", "F32"]:
                if q.lower() in name.lower():
                    quant = q
                    break
            models.append({
                "name": name,
                "file": f.name,
                "path": str(f),
                "size_mb": size_mb,
                "quantization": quant,
            })
        return models

    def list_models(self) -> list[dict]:
        """Alias for scan_models."""
        return self.scan_models()

    def load_model(self, model_name: str) -> bool:
        """Load a model by name (filename stem). Unloads current first."""
        with self._lock:
            self.unload()
            path = self._find_model(model_name)
            if path is None:
                logger.error("Model not found: %s", model_name)
                return False
            if self.use_subprocess:
                return self._load_subprocess(path)
            backend = LocalInferenceBackend(path, self.gpu_layers, self.ctx_size)
            if backend.load():
                self._backend = backend
                return True
            return False

    def _load_subprocess(self, path: Path) -> bool:
        """Load model in a separate process for GPU memory isolation."""
        try:
            from .model_client import SubprocessModelClient
            client = SubprocessModelClient(
                path, self.gpu_layers, self.ctx_size, self.python
            )
            # Run start in a new event loop since we might be called from sync context
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        future = pool.submit(asyncio.run, client.start())
                        return future.result(timeout=120)
                else:
                    return loop.run_until_complete(client.start())
            except RuntimeError:
                return asyncio.run(client.start())
        except ImportError:
            logger.error("model_client not available for subprocess mode")
            return False
        except Exception as exc:
            logger.error("Failed to start model subprocess: %s", exc)
            return False

    def unload(self) -> None:
        """Unload current model."""
        if self._backend is not None:
            self._backend.unload()
            self._backend = None
        if self._subprocess_client is not None:
            try:
                asyncio.run(self._subprocess_client.stop())
            except Exception:
                pass
            self._subprocess_client = None

    def get_backend(self) -> LocalInferenceBackend | None:
        """Get the currently loaded backend (or None)."""
        return self._backend

    def get_subprocess_client(self):
        """Get the subprocess client if using subprocess mode."""
        return self._subprocess_client

    def get_loaded_model_info(self) -> dict | None:
        """Get info about the currently loaded model."""
        if self._backend is None:
            return None
        return self._backend.get_model_info()

    def auto_select_model(self, vram_budget_mb: int = 3000) -> str | None:
        """Pick the largest model that fits in VRAM budget."""
        models = self.scan_models()
        fitting = [m for m in models if m["size_mb"] <= vram_budget_mb]
        if not fitting:
            return None
        # Pick largest
        fitting.sort(key=lambda m: m["size_mb"], reverse=True)
        return fitting[0]["name"]

    def _find_model(self, name: str) -> Path | None:
        """Find a .gguf file by name (stem or full filename)."""
        # Try exact stem match
        for f in self.models_dir.glob("*.gguf"):
            if f.stem == name or f.name == name:
                return f
        # Try prefix match
        for f in self.models_dir.glob("*.gguf"):
            if name.lower() in f.stem.lower():
                return f
        return None
