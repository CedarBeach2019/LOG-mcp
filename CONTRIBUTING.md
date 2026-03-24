# Contributing to LOG-mcp

## Quick Start

1. Fork the repo
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/LOG-mcp.git`
3. Install: `pip install -e ".[dev]"`
4. Run tests: `pytest tests/ -v`
5. Make your changes
6. Add tests for new functionality
7. Submit a PR

## Code Style

- Python 3.10+
- Run `ruff check .` before committing
- Docstrings on public functions
- Type hints on function signatures

## Project Structure

```
vault/core.py      → Core engine (RealLog, Dehydrator, Rehydrator)
vault/cli.py       → CLI interface
vault/archiver.py  → Session archiving + gnosis
mcp/server.py      → MCP server (agent interface)
scouts/            → AI provider connectors
cloudflare/worker/ → Cloudflare Worker (privacy proxy)
docker/            → Container deployment
```

## Adding PII Patterns

Edit `vault/core.py` → `Dehydrator.detect_entities()`:

```python
# Add to self.patterns dict
'new_type': re.compile(r'pattern_here', re.IGNORECASE),
```

Then add the type prefix to `_generate_entity_id()` and tests in `tests/test_extended.py`.

## Adding a Scout Connector

1. Create `scouts/your_provider.py` extending `scouts/base.py:BaseScout`
2. Implement `send()` and `receive()` methods
3. Add to `mcp/server.py` tool list if needed
4. Add tests

## Cloudflare Deployment

1. Add `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `AI_API_KEY` to GitHub repo secrets
2. Push to `main` — auto-deploys via GitHub Actions
3. Or deploy manually: `cd cloudflare/worker && wrangler deploy`

## Docker

```bash
docker compose -f docker/docker-compose.yml up -d              # Vault only
docker compose -f docker/docker-compose.yml --profile local-llm up -d  # + Ollama
docker compose -f docker/docker-compose.yml --profile tunnel up -d     # + CF Tunnel
```

## Testing

```bash
pytest tests/ -v                          # Unit tests (37)
python tests/demo_e2e.py                  # End-to-end scenarios (46 checks)
docker run --rm log-mcp:latest log status  # Container smoke test
```

## Issues

Bug reports and feature requests welcome. Use GitHub Issues.

## License

MIT
