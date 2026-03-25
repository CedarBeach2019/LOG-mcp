# docker/

Docker deployment for LOG-mcp.

## Files

| File | Purpose |
|------|---------|
| `Dockerfile` | Multi-stage build: Python 3.10-slim, pip install, copy source, expose 8000 |
| `docker-compose.yml` | Service definition with env vars, volume mounts, port mapping |

## Quick Start

```bash
# Build
docker build -t log-mcp -f docker/Dockerfile .

# Run (minimal)
docker run -p 8000:8000 \
  -e LOG_PASSPHRASE=your-secret \
  -e LOG_API_KEY=sk-your-key \
  log-mcp

# Run with docker-compose
cd docker
LOG_PASSPHRASE=your-secret LOG_API_KEY=sk-your-key docker compose up -d
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LOG_PASSPHRASE` | Yes | — | Login passphrase |
| `LOG_API_KEY` | Yes | — | DeepSeek API key |
| `LOG_DB_PATH` | No | `~/.log/vault/reallog.db` | SQLite database path |
| `LOG_CHEAP_MODEL_ENDPOINT` | No | `https://api.deepseek.com/v1/chat/completions` | Cheap model endpoint |
| `LOG_CHEAP_MODEL_NAME` | No | `deepseek-chat` | Cheap model name |
| `LOG_ESCALATION_MODEL_ENDPOINT` | No | Same as cheap | Escalation model endpoint |
| `LOG_ESCALATION_MODEL_NAME` | No | `deepseek-reasoner` | Escalation model name |
| `LOG_PRIVACY_MODE` | No | `true` | Enable PII stripping |
| `LOG_CACHE_ENABLED` | No | `true` | Enable semantic cache |
| `LOG_LOCAL_GPU_LAYERS` | No | `0` | GPU layers for local inference (0=CPU) |
| `LOG_LOCAL_MODELS_DIR` | No | `~/.log/models/` | Directory for .gguf models |

## Volumes

Mount `~/.log/` to persist data across container restarts:

```bash
docker run -v ~/.log:/root/.log -p 8000:8000 log-mcp
```

## Notes

- Local GPU inference (llama-cpp-python) is **not included** in the Docker image — it requires NVIDIA GPU and CUDA toolkit. Use the bare-metal setup for GPU inference.
- The container is ~150MB (Python slim + dependencies).
- No health check configured yet (add `HEALTHCHECK` to Dockerfile for production).
