"""Model manager — scans for .gguf files, manages loading/unloading, auto-selects."""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from .local_inference import LocalInferenceBackend

logger = logging.getLogger(__name__)


class ModelManager:
    """Manages available local models and hot-swapping."""

    def __init__(self, models_dir: Path, gpu_layers: int = -1, ctx_size: int = 4096):
        self.models_dir = Path(models_dir)
        self.gpu_layers = gpu_layers
        self.ctx_size = ctx_size
        self._backend: LocalInferenceBackend | None = None
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
            backend = LocalInferenceBackend(path, self.gpu_layers, self.ctx_size)
            if backend.load():
                self._backend = backend
                return True
            return False

    def unload(self) -> None:
        """Unload current model."""
        if self._backend is not None:
            self._backend.unload()
            self._backend = None

    def get_backend(self) -> LocalInferenceBackend | None:
        """Get the currently loaded backend (or None)."""
        return self._backend

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
