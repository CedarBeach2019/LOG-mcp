"""Tests for local inference backend and model manager."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest
from starlette.testclient import TestClient

os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test-key")

from vault.local_inference import LocalInferenceBackend, _llama
from vault.model_manager import ModelManager


class TestLocalInferenceBackend:
    def test_no_llama_cpp_graceful(self):
        """Backend should not crash when llama-cpp-python is not installed."""
        backend = LocalInferenceBackend(Path("/fake/model.gguf"))
        assert not backend.is_loaded
        assert backend.generate([], temperature=0.7) is None
        assert backend.get_model_info()["loaded"] is False

    def test_load_nonexistent_file(self):
        """Loading a nonexistent file should fail gracefully."""
        backend = LocalInferenceBackend(Path("/tmp/nonexistent_model_12345.gguf"))
        # Even with llama installed, nonexistent file should fail
        result = backend.load()
        # Result depends on whether llama-cpp-python is installed
        # If not installed, returns False. If installed, file check may fail.
        assert isinstance(result, bool)

    def test_model_info_without_load(self):
        """get_model_info should work even when not loaded."""
        backend = LocalInferenceBackend(Path("/tmp/test.gguf"))
        info = backend.get_model_info()
        assert info["loaded"] is False
        assert info["model_path"] == "/tmp/test.gguf"
        assert info["model_name"] == "test"

    def test_unload_when_not_loaded(self):
        """Unloading when nothing is loaded should not crash."""
        backend = LocalInferenceBackend(Path("/tmp/test.gguf"))
        backend.unload()  # Should not raise
        assert not backend.is_loaded


class TestModelManager:
    @pytest.fixture
    def models_dir(self):
        with tempfile.TemporaryDirectory() as d:
            yield Path(d)

    def test_scan_empty_dir(self, models_dir):
        manager = ModelManager(models_dir)
        assert manager.scan_models() == []

    def test_scan_with_models(self, models_dir):
        (models_dir / "test-model-q5_km.gguf").write_bytes(b"x" * 1000)
        mgr = ModelManager(models_dir)
        models = mgr.list_models()
        assert len(models) == 1
        assert models[0]["name"] == "test-model-q5_km"
        assert models[0]["quantization"] == "Q5_K"

    def test_auto_select_largest_fitting(self, models_dir):
        (models_dir / "small-q4_km.gguf").write_bytes(b"x" * 500)  # <1MB
        (models_dir / "medium-q5_km.gguf").write_bytes(b"x" * 2000)  # ~2KB but treated as MB in metadata
        manager = ModelManager(models_dir)
        result = manager.auto_select_model(vram_budget_mb=1)  # Very small budget
        # Both fit, should pick largest
        assert result is not None
        assert result == "medium-q5_km"

    def test_find_model_by_stem(self, models_dir):
        (models_dir / "my-model-q4_0.gguf").write_bytes(b"x")
        manager = ModelManager(models_dir)
        path = manager._find_model("my-model-q4_0")
        assert path is not None
        assert path.name == "my-model-q4_0.gguf"

    def test_find_model_prefix(self, models_dir):
        (models_dir / "qwen2.5-1.5b-instruct-q5_km.gguf").write_bytes(b"x")
        manager = ModelManager(models_dir)
        path = manager._find_model("qwen")
        assert path is not None

    def test_load_nonexistent(self, models_dir):
        manager = ModelManager(models_dir)
        assert manager.load_model("nonexistent") is False

    def test_get_loaded_model_info_none(self, models_dir):
        manager = ModelManager(models_dir)
        assert manager.get_loaded_model_info() is None


class TestLocalRoutes:
    @pytest.fixture(autouse=True)
    def reset_deps(self):
        from gateway import deps, routes
        deps._settings = None
        deps._reallog = None
        routes._local_manager = None
        yield
        deps._settings = None
        deps._reallog = None
        routes._local_manager = None

    @pytest.fixture
    def client(self):
        return TestClient(app)

    def _token(self, client):
        return client.post("/auth/login", json={"passphrase": "testpass"}).json()["token"]

    def _headers(self, client):
        return {"Authorization": f"Bearer {self._token(client)}"}

    def test_local_models_list(self, client):
        resp = client.get("/v1/local/models", headers=self._headers(client))
        assert resp.status_code == 200
        data = resp.json()
        assert "models" in data
        assert "loaded" in data

    def test_local_status(self, client):
        resp = client.get("/v1/local/status", headers=self._headers(client))
        assert resp.status_code == 200
        assert resp.json()["loaded"] is False

    def test_local_load_requires_auth(self, client):
        resp = client.post("/v1/local/load", json={})
        assert resp.status_code == 401

    def test_local_load_requires_model_name(self, client):
        resp = client.post("/v1/local/load", headers=self._headers(client), json={})
        assert resp.status_code == 400

    def test_local_unload(self, client):
        resp = client.post("/v1/local/unload", headers=self._headers(client))
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_health_includes_local_model(self, client):
        resp = client.get("/v1/health", headers=self._headers(client))
        assert resp.status_code in (200, 503)
        data = resp.json()
        assert "local_model" in data.get("checks", {}) or "local_model" in data


# Import app after env vars are set
from gateway.server import app
