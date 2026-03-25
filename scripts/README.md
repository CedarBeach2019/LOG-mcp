# scripts/

Utility scripts for LOG-mcp development and testing.

## Files

### `vault-init.sh`
Initializes the vault database with schema and default data. Run once on first deploy.

```bash
bash scripts/vault-init.sh
```

### `test_local_inference.py`
Integration test for llama-cpp-python local inference on the Jetson. Tests:
- Model loading (with various GPU layer counts)
- Single inference
- Streaming generation
- Embeddings
- Prompt caching

**Requires:** llama-cpp-python built with CUDA support, a `.gguf` model in `~/.log/models/`.

```bash
python scripts/test_local_inference.py
```

## Adding Scripts

- Keep scripts in this folder, not in the project root
- Use `#!/usr/bin/env python3` shebang for Python scripts
- Document required environment variables in the script header
- Scripts should be idempotent (safe to run multiple times)
