"""Local model lifecycle — download, quantization, hot-swap."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

logger = logging.getLogger("vault.model_lifecycle")


# Recommended models with available quantizations
MODEL_CATALOG: dict[str, dict] = {
    "qwen2.5-1.5b-instruct": {
        "repo": "Qwen/Qwen2.5-1.5B-Instruct-GGUF",
        "display": "Qwen 2.5 1.5B Instruct",
        "size_gb": 1.1,
        "recommended_quant": "q5_k_m",
        "available_quants": ["q4_k_m", "q5_k_m", "q8_0"],
        "context": 4096,
    },
    "qwen2.5-3b-instruct": {
        "repo": "Qwen/Qwen2.5-3B-Instruct-GGUF",
        "display": "Qwen 2.5 3B Instruct",
        "size_gb": 2.0,
        "recommended_quant": "q5_k_m",
        "available_quants": ["q4_k_m", "q5_k_m", "q8_0"],
        "context": 4096,
    },
    "phi-4-mini-instruct": {
        "repo": "microsoft/Phi-4-mini-instruct-GGUF",
        "display": "Phi-4 Mini Instruct",
        "size_gb": 2.3,
        "recommended_quant": "q4_k_m",
        "available_quants": ["q4_k_m", "q5_k_m"],
        "context": 4096,
    },
    "llama-3.2-1b-instruct": {
        "repo": "huggingface/llama-3.2-1b-instruct-GGUF",
        "display": "Llama 3.2 1B Instruct",
        "size_gb": 0.8,
        "recommended_quant": "q5_k_m",
        "available_quants": ["q4_k_m", "q5_k_m"],
        "context": 4096,
    },
    "all-minilm-l6-v2": {
        "repo": "CompendiumLabs/bge-small-en-v1.5-gguf",
        "display": "BGE Small EN v1.5 (embeddings)",
        "size_gb": 0.04,
        "recommended_quant": "q8_0",
        "available_quants": ["q8_0"],
        "context": 512,
    },
}


def get_available_models(models_dir: Path) -> list[dict]:
    """List all .gguf files in the models directory."""
    models = []
    for f in sorted(models_dir.glob("*.gguf")):
        # Extract name from filename
        stem = f.stem.lower()
        # Try to match against catalog
        catalog_match = None
        for key, info in MODEL_CATALOG.items():
            if key.replace("-", "") in stem.replace("-", "").replace("_", ""):
                catalog_match = key
                break

        models.append({
            "name": f.stem,
            "filename": f.name,
            "size_mb": round(f.stat().st_size / (1024 * 1024), 1),
            "catalog_match": catalog_match,
            "display": MODEL_CATALOG[catalog_match]["display"] if catalog_match else f.stem,
        })
    return models


def estimate_vram(model_size_gb: float, total_system_gb: float = 8.0) -> dict:
    """Estimate GPU memory allocation for a model."""
    # Assume shared memory architecture (Jetson)
    # Leave ~2GB for system + uvicorn + KV cache
    available_for_model = total_system_gb - 2.5  # conservative
    if model_size_gb <= available_for_model:
        return {
            "fits_on_gpu": True,
            "model_size_gb": model_size_gb,
            "recommended_layers": -1,  # all on GPU
            "available_gb": round(available_for_model, 1),
        }
    else:
        # Estimate how many layers fit
        ratio = available_for_model / model_size_gb
        return {
            "fits_on_gpu": False,
            "model_size_gb": model_size_gb,
            "recommended_layers": max(1, int(ratio * 20)),  # rough estimate
            "available_gb": round(available_for_model, 1),
            "cpu_offload_gb": round(model_size_gb - available_for_model, 1),
        }


def suggest_quantization(available_vram_gb: float = 4.0) -> str:
    """Suggest quantization based on available VRAM."""
    if available_vram_gb >= 3.0:
        return "q5_k_m"  # good quality/size ratio
    elif available_vram_gb >= 2.0:
        return "q4_k_m"  # smaller but still decent
    else:
        return "q3_k_m"  # very small


def build_download_url(repo: str, quant: str) -> str:
    """Build the HuggingFace download URL for a GGUF file."""
    # HuggingFace GGUF repos typically have files like:
    # qwen2.5-1.5b-instruct-q5_k_m.gguf
    # or */*.gguf pattern
    filename_base = repo.split("/")[-1].replace("-GGUF", "").lower()
    return f"https://huggingface.co/{repo}/resolve/main/{filename_base}-{quant}.gguf"


def download_model(model_key: str, models_dir: Path, quant: str | None = None,
                   token: str | None = None) -> dict:
    """Download a model from HuggingFace.

    Returns {"success": bool, "path": str, "error": str}
    """
    if model_key not in MODEL_CATALOG:
        return {"success": False, "error": f"Unknown model: {model_key}. Available: {list(MODEL_CATALOG.keys())}"}

    info = MODEL_CATALOG[model_key]
    quant = quant or info["recommended_quant"]
    if quant not in info["available_quants"]:
        return {"success": False, "error": f"Quantization {quant} not available. Options: {info['available_quants']}"}

    url = build_download_url(info["repo"], quant)
    filename = f"{info['repo'].split('/')[-1].lower().replace('-gguf', '')}-{quant}.gguf"
    dest = models_dir / filename

    if dest.exists():
        return {"success": True, "path": str(dest), "message": "Already downloaded", "cached": True}

    # Download using curl or wget
    logger.info("Downloading %s (%s) from %s", info["display"], quant, url)

    headers = []
    if token:
        headers.extend(["-H", f"Authorization: Bearer {token}"])

    try:
        cmd = ["curl", "-L", "-f", "--progress-bar", "-o", str(dest)]
        if headers:
            cmd.extend(headers)
        cmd.append(url)

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            dest.unlink(missing_ok=True)
            return {"success": False, "error": f"Download failed: {result.stderr[:200]}"}

        size_mb = round(dest.stat().st_size / (1024 * 1024), 1)
        logger.info("Downloaded %s (%.1f MB)", filename, size_mb)
        return {"success": True, "path": str(dest), "size_mb": size_mb}

    except subprocess.TimeoutExpired:
        dest.unlink(missing_ok=True)
        return {"success": False, "error": "Download timed out (10 min)"}
    except Exception as exc:
        dest.unlink(missing_ok=True)
        return {"success": False, "error": str(exc)}


def hot_swap_model(model_manager, new_model_name: str, new_model_path: Path) -> bool:
    """Hot-swap to a new model without downtime.

    Strategy: load new model first, then unload old. If new model fails,
    keep old model running.
    """
    import logging
    logger = logging.getLogger("vault.model_lifecycle")

    current_info = model_manager.get_loaded_model_info()
    current_model = current_info.get("model_name") if current_info else None

    try:
        # Load new model
        logger.info("Hot-swapping: loading %s...", new_model_name)
        if model_manager.use_subprocess:
            from vault.model_client import SubprocessModelClient
            # For subprocess, we need to stop old and start new
            model_manager.unload()
            client = SubprocessModelClient(new_model_path, model_manager.gpu_layers, model_manager.ctx_size)
            import asyncio
            success = asyncio.run(client.start())
            if success:
                model_manager._subprocess_client = client
                logger.info("Hot-swap complete: %s → %s", current_model, new_model_name)
                return True
            return False
        else:
            # In-process: load new, unload old
            from vault.local_inference import LocalInferenceBackend
            new_backend = LocalInferenceBackend(new_model_path, model_manager.gpu_layers, model_manager.ctx_size)
            if not new_backend.load():
                logger.error("Hot-swap failed: new model %s failed to load", new_model_name)
                return False

            old_backend = model_manager.get_backend()
            model_manager._backend = new_backend
            if old_backend:
                old_backend.unload()
            logger.info("Hot-swap complete: %s → %s", current_model, new_model_name)
            return True

    except Exception as exc:
        logger.error("Hot-swap failed: %s", exc)
        return False
