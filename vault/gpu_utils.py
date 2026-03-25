"""GPU memory utilities for Jetson and other NVIDIA devices."""

from __future__ import annotations

import logging
import subprocess

logger = logging.getLogger(__name__)


def get_gpu_memory_info() -> dict:
    """Get GPU memory info. Works on Jetson and desktop NVIDIA GPUs."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total,memory.used,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            return {
                "total_mb": int(parts[0]),
                "used_mb": int(parts[1]),
                "free_mb": int(parts[2]),
            }
    except Exception:
        pass

    # Jetson fallback: tegrastats (one-shot)
    try:
        result = subprocess.run(
            ["tegrastats", "--interval", "1000", "--count", "1"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            # Parse RAM info from tegrastats output
            # Format: "RAM <used>/<total>MB, SWAP ..."
            import re
            ram_match = re.search(r'RAM (\d+)/(\d+)', result.stdout)
            if ram_match:
                used = int(ram_match.group(1))
                total = int(ram_match.group(2))
                # Jetson has unified memory — total = GPU + CPU
                # GPU typically gets ~1/3 to ~1/2 depending on config
                gpu_reserved = max(256, total // 4)  # rough estimate
                return {
                    "total_mb": total,
                    "used_mb": used,
                    "free_mb": total - used,
                    "gpu_available_mb": max(0, (total - used) - 512),  # leave 512MB for OS
                    "source": "tegrastats",
                }
    except Exception:
        pass

    return {"total_mb": 0, "used_mb": 0, "free_mb": 0, "source": "unknown"}


def calculate_optimal_gpu_layers(model_size_mb: int, ctx_size: int = 2048, safety_margin_mb: int = 512) -> int:
    """Calculate how many layers to offload to GPU given available memory.

    Heuristic:
    - Each layer costs ~model_size_mb / total_layers in VRAM
    - KV cache costs ~2 bytes * num_layers * hidden_dim * ctx_size / 1024^2 MB
    - We leave safety_margin_mb for OS and other processes

    Returns recommended gpu_layers count (0 = CPU only).
    """
    info = get_gpu_memory_info()
    available = info.get("gpu_available_mb", info.get("free_mb", 0))
    budget = max(0, available - safety_margin_mb)

    if budget <= 0 or model_size_mb <= 0:
        return 0

    # Rough: 1.5B model has ~28 transformer layers
    # Model size / layers ≈ per-layer cost
    # For a 1.5B model: ~200MB model + ~300MB KV cache at ctx=2048
    estimated_layers = int(model_size_mb / 8)  # rough estimate of layer count from size
    per_layer_mb = model_size_mb / max(estimated_layers, 1)

    # KV cache: ~2 * hidden_dim * num_layers * ctx_size * 2 bytes / 1024^2
    # For 1.5B: hidden=2048, layers=28, ctx=2048 → ~200MB
    kv_cache_mb = 0.2 * ctx_size / 1024  # rough: scales linearly with ctx

    max_layers_for_model = int((budget * 0.6) / per_layer_mb)  # 60% for model weights
    max_layers_for_kv = int((budget * 0.4) / (kv_cache_mb / max(estimated_layers, 1))) if kv_cache_mb > 0 else 999

    return min(max_layers_for_model, max_layers_for_kv, estimated_layers, 99)
