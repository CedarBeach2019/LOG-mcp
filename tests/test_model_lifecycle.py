"""Tests for model lifecycle."""

import os
os.environ.setdefault("LOG_PASSPHRASE", "testpass")
os.environ.setdefault("LOG_API_KEY", "sk-test")

import pytest
from pathlib import Path
from vault.model_lifecycle import (
    MODEL_CATALOG,
    estimate_vram,
    suggest_quantization,
    build_download_url,
    get_available_models,
)


class TestEstimateVRAM:
    def test_small_model_fits(self):
        result = estimate_vram(1.0, total_system_gb=8.0)
        assert result["fits_on_gpu"] is True
        assert result["recommended_layers"] == -1

    def test_large_model_cpu_offload(self):
        result = estimate_vram(6.0, total_system_gb=8.0)
        assert result["fits_on_gpu"] is False
        assert result["cpu_offload_gb"] > 0

    def test_exact_fit(self):
        result = estimate_vram(5.5, total_system_gb=8.0)
        # 8 - 2.5 = 5.5 available, so it just fits
        assert result["fits_on_gpu"] is True


class TestSuggestQuantization:
    def test_high_vram(self):
        assert suggest_quantization(4.0) == "q5_k_m"

    def test_medium_vram(self):
        assert suggest_quantization(2.5) == "q4_k_m"

    def test_low_vram(self):
        assert suggest_quantization(1.0) == "q3_k_m"


class TestBuildDownloadUrl:
    def test_qwen_url(self):
        url = build_download_url("Qwen/Qwen2.5-1.5B-Instruct-GGUF", "q5_k_m")
        assert "huggingface.co" in url
        assert "q5_k_m.gguf" in url

    def test_phi_url(self):
        url = build_download_url("microsoft/Phi-4-mini-instruct-GGUF", "q4_k_m")
        assert "huggingface.co" in url
        assert "q4_k_m.gguf" in url


class TestGetAvailableModels:
    def test_empty_dir(self, tmp_path):
        models = get_available_models(tmp_path)
        assert models == []

    def test_finds_gguf(self, tmp_path):
        (tmp_path / "test-model-q5_k_m.gguf").write_bytes(b"\x00" * 1024 * 1024)
        models = get_available_models(tmp_path)
        assert len(models) == 1
        assert models[0]["filename"] == "test-model-q5_k_m.gguf"
        assert models[0]["size_mb"] >= 1.0

    def test_catalog_match(self, tmp_path):
        (tmp_path / "qwen2.5-1.5b-instruct-q5_k_m.gguf").write_bytes(b"\x00" * 100)
        models = get_available_models(tmp_path)
        assert models[0]["catalog_match"] == "qwen2.5-1.5b-instruct"


class TestModelCatalog:
    def test_has_qwen(self):
        assert "qwen2.5-1.5b-instruct" in MODEL_CATALOG

    def test_has_embedding_model(self):
        assert "all-minilm-l6-v2" in MODEL_CATALOG

    def test_catalog_structure(self):
        for key, info in MODEL_CATALOG.items():
            assert "repo" in info
            assert "display" in info
            assert "recommended_quant" in info
            assert "available_quants" in info

    def test_download_model_unknown(self):
        from vault.model_lifecycle import download_model
        result = download_model("nonexistent-model", Path("/tmp"))
        assert result["success"] is False
        assert "Unknown model" in result["error"]

    def test_download_model_bad_quant(self):
        from vault.model_lifecycle import download_model
        result = download_model("qwen2.5-1.5b-instruct", Path("/tmp"), quant="q99_invalid")
        assert result["success"] is False
        assert "not available" in result["error"]


class TestDownloadCached:
    def test_already_downloaded(self, tmp_path):
        from vault.model_lifecycle import download_model
        # Build the expected filename that download_model would look for
        # The function builds: repo.split("/")[-1].lower().replace("-gguf", "") + "-" + quant + ".gguf"
        # For qwen2.5-1.5b-instruct: "qwen2.5-1.5b-instruct-gguf" -> "qwen2.5-1.5b-instruct-gguf-q5_k_m.gguf"
        fake_file = tmp_path / "qwen2.5-1.5b-instruct-q5_k_m.gguf"
        fake_file.write_bytes(b"\x00" * 100)
        result = download_model("qwen2.5-1.5b-instruct", tmp_path, "q5_k_m")
        assert result["success"] is True
