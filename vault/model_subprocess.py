"""Model subprocess server — runs llama-cpp-python in a separate process.

Solves the Jetson 8GB shared memory problem: uvicorn and the model can't
coexist in GPU memory. This spawns a lightweight server process that loads
the model exclusively, communicates via stdin/stdout JSON protocol.

Usage:
    python -m vault.model_subprocess --model path/to/model.gguf [--gpu-layers 20] [--ctx 2048]

Protocol (newline-delimited JSON over stdin/stdout):
    IN:  {"action": "generate", "prompt": "...", "max_tokens": 256, "temperature": 0.7}
    IN:  {"action": "embed", "text": "..."}
    IN:  {"action": "ping"}
    IN:  {"action": "unload"}
    OUT: {"status": "ok", "text": "...", "tokens": 42, "time_ms": 123}
    OUT: {"status": "ok", "embedding": [0.1, 0.2, ...]}
    OUT: {"status": "ok", "pong": true}
    OUT: {"status": "error", "error": "..."}
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("model_subprocess")


def main():
    """Entry point when run as subprocess."""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Path to GGUF model file")
    parser.add_argument("--gpu-layers", type=int, default=-1, help="GPU layers (-1 = all)")
    parser.add_argument("--ctx", type=int, default=2048, help="Context size")
    args = parser.parse_args()

    model_path = Path(args.model)
    if not model_path.exists():
        _send({"status": "error", "error": f"Model not found: {model_path}"})
        sys.exit(1)

    try:
        from llama_cpp import Llama
    except ImportError:
        _send({"status": "error", "error": "llama-cpp-python not installed"})
        sys.exit(1)

    # Load model
    logger.info("Loading model: %s (gpu_layers=%d, ctx=%d)", model_path.name, args.gpu_layers, args.ctx)
    try:
        model = Llama(
            str(model_path),
            n_gpu_layers=args.gpu_layers,
            n_ctx=args.ctx,
            verbose=False,
        )
        logger.info("Model loaded successfully")
        _send({"status": "ok", "model": model_path.name, "loaded": True})
    except Exception as exc:
        logger.error("Failed to load model: %s", exc)
        _send({"status": "error", "error": str(exc)})
        sys.exit(1)

    # Message loop
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _send({"status": "error", "error": "invalid JSON"})
            continue

        action = msg.get("action", "")
        try:
            if action == "ping":
                _send({"status": "ok", "pong": True})
            elif action == "generate":
                _handle_generate(model, msg)
            elif action == "embed":
                _handle_embed(model, msg)
            elif action == "unload":
                _send({"status": "ok", "unloaded": True})
                break
            else:
                _send({"status": "error", "error": f"unknown action: {action}"})
        except Exception as exc:
            logger.error("Error handling %s: %s", action, exc)
            _send({"status": "error", "error": str(exc)})

    logger.info("Model subprocess exiting")


def _handle_generate(model, msg: dict):
    t0 = time.time()
    prompt = msg.get("prompt", "")
    max_tokens = msg.get("max_tokens", 256)
    temperature = msg.get("temperature", 0.7)

    output = model(
        prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        stop=["</s>", "\n\n\n"],
        echo=False,
    )
    text = output["choices"][0]["text"].strip()
    elapsed = (time.time() - t0) * 1000
    _send({
        "status": "ok",
        "text": text,
        "tokens": output.get("usage", {}).get("completion_tokens", 0),
        "time_ms": round(elapsed, 1),
    })


def _handle_embed(model, msg: dict):
    t0 = time.time()
    text = msg.get("text", "")
    embedding = model.embed(text)
    elapsed = (time.time() - t0) * 1000
    _send({
        "status": "ok",
        "embedding": embedding,
        "time_ms": round(elapsed, 1),
    })


def _send(data: dict):
    """Send a JSON response to stdout."""
    sys.stdout.write(json.dumps(data) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
