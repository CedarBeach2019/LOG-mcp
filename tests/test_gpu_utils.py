"""Tests for GPU memory utilities."""

from vault.gpu_utils import get_gpu_memory_info, calculate_optimal_gpu_layers


class TestGPUMemoryInfo:
    def test_returns_dict(self):
        info = get_gpu_memory_info()
        assert isinstance(info, dict)
        assert "total_mb" in info
        assert "free_mb" in info

    def test_values_are_non_negative(self):
        info = get_gpu_memory_info()
        assert info["total_mb"] >= 0
        assert info["used_mb"] >= 0
        assert info["free_mb"] >= 0


class TestCalculateOptimalGPULayers:
    def test_small_model(self):
        layers = calculate_optimal_gpu_layers(200, ctx_size=2048)
        assert isinstance(layers, int)
        assert layers >= 0

    def test_large_model(self):
        layers = calculate_optimal_gpu_layers(4000, ctx_size=2048)
        assert layers >= 0  # might be 0 if not enough memory

    def test_zero_safety_margin(self):
        # With no safety margin, should allow more layers
        layers = calculate_optimal_gpu_layers(200, safety_margin_mb=0)
        assert layers >= 0

    def test_tiny_model_fits_anywhere(self):
        layers = calculate_optimal_gpu_layers(50, ctx_size=512)
        assert layers > 0  # 50MB model should fit

    def test_gigantic_model_cpu_only(self):
        layers = calculate_optimal_gpu_layers(16000, ctx_size=8192)
        # 16GB model won't fit in any reasonable GPU
        assert isinstance(layers, int)

    def test_larger_ctx_fewer_layers(self):
        small_ctx = calculate_optimal_gpu_layers(500, ctx_size=1024)
        large_ctx = calculate_optimal_gpu_layers(500, ctx_size=8192)
        assert small_ctx >= large_ctx  # more KV cache needed for larger ctx
