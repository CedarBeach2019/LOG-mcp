# tests/

Test suite for LOG-mcp. 194 tests, all passing in ~8 seconds.

## Running

```bash
# All tests
python -m pytest tests/ -q

# Specific file
python -m pytest tests/test_gateway.py -v

# With coverage
python -m pytest tests/ --cov=vault --cov=gateway --cov-report=term-missing
```

## Test Files

| File | Tests | Covers |
|------|------:|--------|
| `test_core.py` | 8 | Dehydrator, Rehydrator, PII detection, session creation |
| `test_extended.py` | 26 | Cross-session entities, edge cases, MCP interface, archiver, Unicode |
| `test_gateway.py` | 7 | Auth, PII protection in upstream requests, rehydration |
| `test_phase2.py` | 30 | Routing classification, chat completions, feedback, preferences, compare mode |
| `test_drafts.py` | 5 | Draft parallel calls, elaboration, ranking storage |
| `test_profiles.py` | 18 | Profile CRUD, defaults, validation, merge logic |
| `test_routing_script.py` | 20 | All routing rules, confidence scores, edge cases |
| `test_semantic_cache.py` | 20 | LRU eviction, TTL, cosine similarity, invalidation |
| `test_local_inference.py` | 17 | Model loading, GPU layers, streaming, embeddings, graceful degradation |
| `test_stats_engine.py` | 17 | Stats computation, routing suggestions, history |
| `test_sessions.py` | 7 | Session CRUD, chat returns session_id, session history |
| `test_gpu_utils.py` | 8 | GPU memory detection, auto layer calculation |
| `test_llm_scorer.py` | 4 | Response quality scoring |
| `test_cli_db.py` | 7 | CLI database operations |
| `conftest.py` | — | Shared pytest configuration |
| `demo_e2e.py` | — | End-to-end demo script |

## Fixture System

All tests use **isolated SQLite databases** via `tmp_path`:

```python
@pytest.fixture(autouse=True)
def reset_deps(tmp_path):
    from gateway.deps import reset_all
    reset_all(str(tmp_path / "test.db"))
    yield
    reset_all()
```

`reset_all()` in `gateway/deps.py` closes the DB connection, clears singletons, and optionally sets `LOG_DB_PATH` to a temp file. This eliminates all "database is locked" race conditions between concurrent tests.

## Mocking

- **`call_model`** — patched at `gateway.routes.call_model` for gateway tests
- **`httpx.AsyncClient`** — mocked for PII protection tests
- **Unique messages** — session tests use `uuid.uuid4()` to avoid semantic cache hits

## What's Not Tested

- Actual DeepSeek API calls (all mocked)
- llama-cpp-python GPU inference (mocked in unit tests, integration test in `scripts/`)
- Browser UI interactions
- Multi-user concurrent access
