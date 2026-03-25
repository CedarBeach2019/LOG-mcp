"""Subprocess model client — communicates with vault.model_subprocess.

Manages lifecycle of the model subprocess:
- Spawns on demand (lazy)
- Health checks via ping
- Auto-restarts on crash
- Clean shutdown on server exit
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("vault.model_client")


class SubprocessModelClient:
    """Client for the model subprocess server."""

    def __init__(self, model_path: str | Path, gpu_layers: int = -1,
                 ctx_size: int = 2048, python: str = "python3"):
        self.model_path = str(Path(model_path).expanduser())
        self.gpu_layers = gpu_layers
        self.ctx_size = ctx_size
        self.python = python
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._ready = False

    async def start(self) -> bool:
        """Start the model subprocess."""
        if self._process and self._process.returncode is None:
            return True

        cmd = [
            self.python, "-m", "vault.model_subprocess",
            "--model", self.model_path,
            "--gpu-layers", str(self.gpu_layers),
            "--ctx", str(self.ctx_size),
        ]

        logger.info("Starting model subprocess: %s", " ".join(cmd))
        try:
            self._process = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Wait for ready message (timeout 120s for model loading)
            try:
                line = await asyncio.wait_for(
                    self._read_line(), timeout=120.0
                )
                data = json.loads(line)
                if data.get("status") != "ok":
                    logger.error("Model failed to start: %s", data.get("error"))
                    await self.stop()
                    return False
                self._ready = True
                logger.info("Model subprocess ready")
                return True
            except asyncio.TimeoutError:
                logger.error("Model subprocess timed out waiting for ready")
                await self.stop()
                return False

        except Exception as exc:
            logger.error("Failed to start model subprocess: %s", exc)
            return False

    async def stop(self):
        """Stop the model subprocess and free GPU memory."""
        if self._process and self._process.returncode is None:
            try:
                await self._send({"action": "unload"})
                self._process.stdin.close()
                await asyncio.wait_for(self._process.wait(), timeout=10.0)
            except Exception:
                self._process.kill()
            logger.info("Model subprocess stopped")
        self._process = None
        self._ready = False

    async def generate(self, prompt: str, max_tokens: int = 256,
                       temperature: float = 0.7) -> dict:
        """Generate text using the model subprocess."""
        async with self._lock:
            if not await self._ensure_alive():
                return {"status": "error", "error": "model not available"}

            await self._send({
                "action": "generate",
                "prompt": prompt,
                "max_tokens": max_tokens,
                "temperature": temperature,
            })
            try:
                line = await asyncio.wait_for(self._read_line(), timeout=60.0)
                return json.loads(line)
            except asyncio.TimeoutError:
                return {"status": "error", "error": "generation timeout"}

    async def embed(self, text: str) -> dict:
        """Get embedding using the model subprocess."""
        async with self._lock:
            if not await self._ensure_alive():
                return {"status": "error", "error": "model not available"}

            await self._send({
                "action": "embed",
                "text": text,
            })
            try:
                line = await asyncio.wait_for(self._read_line(), timeout=30.0)
                return json.loads(line)
            except asyncio.TimeoutError:
                return {"status": "error", "error": "embedding timeout"}

    async def ping(self) -> bool:
        """Health check."""
        if not self._process or self._process.returncode is not None:
            return False
        try:
            await self._send({"action": "ping"})
            line = await asyncio.wait_for(self._read_line(), timeout=5.0)
            data = json.loads(line)
            return data.get("status") == "ok"
        except Exception:
            return False

    async def is_alive(self) -> bool:
        """Check if process is running."""
        return self._process is not None and self._process.returncode is None and self._ready

    # ---- Compatible interface with LocalInferenceBackend ----

    @property
    def is_loaded(self) -> bool:
        """Compatible with LocalInferenceBackend.is_loaded."""
        return self._ready

    async def agenerate(self, messages: list[dict], temperature: float = 0.7,
                        max_tokens: int = 512) -> str | None:
        """Compatible with LocalInferenceBackend.agenerate().
        Converts chat messages to a prompt string for the subprocess."""
        # Convert messages to prompt
        prompt = ""
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt += f"System: {content}\n\n"
            elif role == "user":
                prompt += f"User: {content}\n"
            elif role == "assistant":
                prompt += f"Assistant: {content}\n"
        prompt += "Assistant: "

        result = await self.generate(prompt, max_tokens, temperature)
        if result.get("status") == "ok":
            return result["text"]
        return None

    async def aembed(self, text: str) -> list[float] | None:
        """Compatible with LocalInferenceBackend.embed()."""
        result = await self.embed(text)
        if result.get("status") == "ok":
            return result["embedding"]
        return None

    # ---- Internal ----

    async def _ensure_alive(self) -> bool:
        """Ensure model is running, restart if needed."""
        if self._process and self._process.returncode is not None:
            logger.warning("Model subprocess died, restarting...")
            self._ready = False
            return await self.start()
        if not self._ready:
            return await self.start()
        return True

    async def _send(self, data: dict):
        """Send JSON to subprocess stdin."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("subprocess not running")
        self._process.stdin.write((json.dumps(data) + "\n").encode())
        await self._process.stdin.drain()

    async def _read_line(self) -> str:
        """Read a line from subprocess stdout."""
        if not self._process or not self._process.stdout:
            raise RuntimeError("subprocess not running")
        return (await self._process.stdout.readline()).decode().strip()
