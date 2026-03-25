# LOG-mcp Performance & Architecture Audit

**Date:** 2026-03-25  
**Target:** Jetson Super Orin Nano 8GB (ARM64, 2TB NVMe)  
**Auditor:** Automated systems review

---

## HIGH Impact — Fix Now

### H1. httpx client created per request (TCP handshake on every call)

**File:** `gateway/routes.py`, line ~99 (`_call_model`)

```python
async with httpx.AsyncClient() as client:
    resp = await client.post(...)
```

Every model call opens a new TCP connection (DNS → SYN → TLS if applicable). For DeepSeek API calls with ~500ms model latency, the connection overhead is ~50-100ms per call. In draft mode with 3 profiles, that's 3 unnecessary handshakes.

**Fix:** Create a module-level `httpx.AsyncClient` singleton:

```python
# gateway/routes.py — top of file
_http_client: httpx.AsyncClient | None = None

def get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None or _http_client.is_closed:
        _http_client = httpx.AsyncClient(
            timeout=60.0,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
    return _http_client

# In _call_model:
async with get_http_client() as client:
    resp = await client.post(...)
```

Also applies to `health()` endpoint (lines ~445-460) which creates **two** throwaway clients.

### H2. Regex patterns recompiled on every `detect_entities()` call

**File:** `vault/core.py`, lines ~228-287 (`detect_entities`)

```python
for entity_type, pattern in self.patterns.items():
    for match in re.finditer(pattern, text, re.IGNORECASE):
```

`self.patterns` stores raw strings. `re.finditer(pattern_string, ...)` compiles each pattern on every call. There are 5 base patterns + 6 inline patterns in the method body — 11 compilations per message.

**Fix:** Pre-compile in `__init__`:

```python
def __init__(self, reallog: RealLog, settings=None):
    self.reallog = reallog
    self.settings = settings
    self.compiled_patterns = {
        'email': re.compile(r'...', re.IGNORECASE),
        'phone': re.compile(r'...', re.IGNORECASE),
        # ... all patterns from self.patterns
    }
    self._name_re = re.compile(r'(?<![A-Za-z])([A-Z][a-z]{1,15})...')
    self._address_re = re.compile(r'...', re.IGNORECASE)
    self._passport_res = [re.compile(p) for p in passport_patterns]
    self._chinese_phone_re = re.compile(r'...')
    self._russian_name_re = re.compile(r'...')
    self._chinese_context_re = re.compile(r'...')
```

### H3. SQLite not in WAL mode

**File:** `vault/core.py`, line ~120 (`_get_connection`)

```python
self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
```

Default SQLite journal mode is DELETE, which blocks readers during writes. Every `commit()` in `dehydrate()` briefly locks the DB.

**Fix:** Add after connect:

```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA busy_timeout=5000")
```

`WAL` allows concurrent reads during writes. `busy_timeout` prevents immediate SQLITE_BUSY failures.

### H4. `Dehydrator` and `Rehydrator` instantiated per request

**File:** `gateway/routes.py`, lines ~155-156 (`chat_completions`)

```python
dehydrator = Dehydrator(reallog=reallog)
rehydrator = Rehydrator(reallog=reallog)
```

Both are stateless wrappers around `reallog`. Creating them per request means recompiling regex every time (see H2).

**Fix:** Create once as module-level singletons in `deps.py`:

```python
# gateway/deps.py
_dehydrator: Dehydrator | None = None
_rehydrator: Rehydrator | None = None

def get_dehydrator() -> Dehydrator:
    global _dehydrator
    if _dehydrator is None:
        _dehydrator = Dehydrator(reallog=get_reallog())
    return _dehydrator

def get_rehydrator() -> Rehydrator:
    global _rehydrator
    if _rehydrator is None:
        _rehydrator = Rehydrator(reallog=get_reallog())
    return _rehydrator
```

### H5. `_next_letter_id` does a full table scan on every entity registration

**File:** `vault/core.py`, lines ~330-350

```python
rows = conn.execute(
    "SELECT entity_id FROM pii_map WHERE entity_id LIKE ?",
    (f"{prefix}_%",)
).fetchall()
used_ids = set()
```

This loads ALL entities for a prefix type into memory, then iterates A-Z, AA-ZZ in Python. With 100+ entities, it's loading all of them every time a new entity is registered.

**Fix:** Use `MAX` or a counter:

```python
def _next_letter_id(self, prefix: str) -> str:
    conn = self.reallog._get_connection()
    # Get highest numeric suffix if using numbered scheme, or count
    count = conn.execute(
        "SELECT COUNT(*) FROM pii_map WHERE entity_id LIKE ?",
        (f"{prefix}_%",)
    ).fetchone()[0]
    
    if count < 26:
        letter = chr(ord('A') + count)
        candidate = f"{prefix}_{letter}"
        # Verify it's not taken (defensive)
        exists = conn.execute(
            "SELECT 1 FROM pii_map WHERE entity_id = ?", (candidate,)
        ).fetchone()
        if not exists:
            return candidate
    
    # Fallback to count-based
    return f"{prefix}_{count + 1}"
```

Or better: use a sequence table.

### H6. No rate limiting enforced

**File:** `vault/config.py`, line ~25: `rate_limit: int = 30`

The config field exists but is **never checked** anywhere in the codebase. No middleware, no per-route check.

**Fix:** Add middleware in `gateway/server.py`:

```python
from collections import defaultdict
from time import time

_rate_counts: dict[str, list[float]] = defaultdict(list)

async def rate_limit_middleware(request: Request, call_next):
    # Skip non-authenticated routes
    if request.url.path in ("/auth/login", "/"):
        return await call_next(request)
    
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return await call_next(request)
    
    token = auth[7:]
    now = time()
    window = _rate_counts[token]
    _rate_counts[token] = [t for t in window if now - t < 60]
    
    settings = get_settings()
    if len(_rate_counts[token]) >= settings.rate_limit:
        return JSONResponse({"error": "rate limit exceeded"}, status_code=429)
    
    _rate_counts[token].append(now)
    return await call_next(request)
```

---

## MEDIUM Impact — Next Sprint

### M1. Singleton factories in `deps.py` are not thread-safe

**File:** `gateway/deps.py`, lines ~8-20

```python
_settings: VaultSettings | None = None

def get_settings() -> VaultSettings:
    global _settings
    if _settings is None:
        _settings = VaultSettings()
    return _settings
```

Python's GIL makes this safe for CPython in practice (assignment is atomic), but it's not guaranteed on other runtimes. Also, if two coroutines check `is None` between await points, they'd create two instances (though `asyncio` is single-threaded, so this specific case is safe).

**Fix:** Use `functools.cache` or a lock:

```python
import functools

@functools.cache
def get_settings() -> VaultSettings:
    return VaultSettings()
```

### M2. Mixed connection strategies: persistent + per-call `DatabaseConnection`

**File:** `vault/core.py`

`RealLog._get_connection()` returns a persistent connection (line ~120), but many methods use `DatabaseConnection` context manager (line ~98) which opens a **new** connection each time. This means:

- `_store_entity` uses persistent conn (via lock)
- `_get_entity_by_value` uses persistent conn
- `get_session` uses fresh `DatabaseConnection`
- `rehydrate._get_entity_by_id` uses fresh `DatabaseConnection`

The fresh connections don't have WAL mode or `foreign_keys` PRAGMA set.

**Fix:** Standardize on the persistent connection for reads. Use `DatabaseConnection` only for explicit transaction boundaries:

```python
class DatabaseConnection:
    def __enter__(self):
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        return self.conn
```

### M3. No per-stage timing metrics

**File:** `gateway/routes.py`

Only `t0` captures start time. No breakdown of PII detect → route classify → model call → rehydrate.

**Fix:** Add structured timing:

```python
t_start = time.monotonic()
t_pii = time.monotonic()
# ... dehydrate ...
t_pii_done = time.monotonic()
t_route = time.monotonic()
# ... classify ...
t_route_done = time.monotonic()
t_model = time.monotonic()
# ... call_model ...
t_model_done = time.monotonic()
t_rehydrate = time.monotonic()
# ... rehydrate ...
t_rehydrate_done = time.monotonic()

metrics = {
    "pii_ms": int((t_pii_done - t_pii) * 1000),
    "route_ms": int((t_route_done - t_route) * 1000),
    "model_ms": int((t_model_done - t_model) * 1000),
    "rehydrate_ms": int((t_rehydrate_done - t_rehydrate) * 1000),
    "total_ms": int((t_rehydrate_done - t_start) * 1000),
}
logger.info("request_metrics", extra=metrics)
```

### M4. `serve_index` reads file from disk on every request

**File:** `gateway/routes.py`, line ~40

```python
async def serve_index(request: Request) -> JSONResponse:
    return JSONResponse(
        open(WEB_DIR / "index.html").read(),
        media_type="text/html",
    )
```

Also incorrectly wraps HTML in JSONResponse. Reads from NVMe on every GET `/`.

**Fix:**

```python
_index_html: str | None = None

async def serve_index(request: Request):
    global _index_html
    if _index_html is None:
        _index_html = (WEB_DIR / "index.html").read_text()
    return HTMLResponse(_index_html)
```

### M5. `get_sessions` loads all sessions then filters in Python

**File:** `vault/core.py`, line ~200 (`get_sessions`)

```python
rows = conn.execute(
    "SELECT ... FROM sessions ORDER BY timestamp DESC LIMIT ?",
    (limit,)
).fetchall()
for row in rows:
    metadata = json.loads(row['metadata'])
    if metadata.get('tier') == tier.value:  # Python-side filter
```

If you have 100 sessions and 5 are "hot", this loads and deserializes all 100 JSON metadata blobs.

**Fix:** Store tier as a dedicated column:

```sql
ALTER TABLE sessions ADD COLUMN tier TEXT DEFAULT 'hot';
CREATE INDEX IF NOT EXISTS idx_sessions_tier ON sessions(tier);
```

Then query directly:

```python
rows = conn.execute(
    "SELECT ... FROM sessions WHERE tier = ? ORDER BY timestamp DESC LIMIT ?",
    (tier.value, limit)
).fetchall()
```

### M6. Routing patterns in `routing_script.py` not pre-compiled

**File:** `vault/routing_script.py`, `classify()` function

```python
for pattern in RULES["MANUAL_OVERRIDE"]["patterns"]:
    if re.search(pattern, text_lower):
```

Same issue as H2 — 15+ patterns recompiled per call. Though `classify()` claims ~5ms, pre-compiling would halve that.

**Fix:** Pre-compile at module load:

```python
COMPILED_RULES = {}
for key, rule in RULES.items():
    COMPILED_RULES[key] = {
        "patterns": [re.compile(p) for p in rule["patterns"]],
        "action": rule["action"],
    }

def classify(user_input, ...):
    for pattern in COMPILED_RULES["MANUAL_OVERRIDE"]["patterns"]:
        if pattern.search(text_lower):
            ...
```

### M7. Draft parallel calls use unlimited `asyncio.gather`

**File:** `gateway/routes.py`, line ~280

```python
coros = [_call_draft_profile(settings, api_key, p, dehydrated_messages) for p in profiles]
results = await asyncio.gather(*coros)
```

If a user adds 20 profiles, this fires 20 simultaneous HTTP requests. Should be capped.

**Fix:**

```python
import asyncio

MAX_DRAFTS = 8

async def drafts(request):
    profiles = (body.get("profiles") or get_draft_profiles(settings))[:MAX_DRAFTS]
    semaphore = asyncio.Semaphore(4)
    
    async def limited_call(p):
        async with semaphore:
            return await _call_draft_profile(settings, api_key, p, dehydrated_messages)
    
    results = await asyncio.gather(*[limited_call(p) for p in profiles])
```

### M8. Health endpoint has weak upstream check

**File:** `gateway/routes.py`, lines ~440-460

The "cheap model" health check tries to strip `/chat/completions` and `/v1` from the endpoint URL, but the logic is fragile and may hit a non-existent endpoint. The response status isn't even checked (just catches exceptions).

**Fix:** Use a proper health probe:

```python
# Just check if we can reach the host
base = settings.cheap_model_endpoint.split("/v1/")[0]
async with get_http_client() as client:
    resp = await client.get(f"{base}/models", timeout=3.0)
    results["cheap"] = resp.status_code == 200
```

---

## LOW Impact — Nice to Have

### L1. Rust/PyO3 rewrite candidates

**PII detection (H2 already fixes most of this):** After pre-compiling regexes, `detect_entities()` on a typical message (~200 chars) takes ~0.5ms in Python. This is negligible compared to the 500-5000ms model call. **Rust rewrite would be premature.**

**Routing classification (M6 already fixes most of this):** After pre-compiling, `classify()` takes ~1-2ms. The model call is 100-1000x slower. **Not worth rewriting.**

**Verdict:** Python is fast enough for both hot paths. The bottleneck is network I/O to DeepSeek. Focus on connection pooling (H1) instead.

### L2. Jetson GPU memory monitoring

The current `/v1/health` endpoint doesn't check GPU or memory status. On a Jetson with 8GB shared RAM, GPU memory pressure from llama.cpp could cause issues.

**Fix:** Add to health endpoint:

```python
import subprocess

def get_gpu_stats() -> dict:
    try:
        out = subprocess.check_output(
            ["tegrastats", "--interval", "100", "--count", "1"],
            timeout=1, stderr=subprocess.DEVNULL
        ).decode()
        # Parse RAM/GPU usage from tegrastats output
        return {"tegrastats": out.strip()}
    except Exception:
        return {"tegrastats": "unavailable"}
```

### L3. Request/response logging for optimizer

No structured logging of inputs/outputs for the Phase 3 ML optimizer. The `interactions` table stores this, but there's no query to extract training data.

**Fix:** Add an admin endpoint:

```python
async def export_training_data(request):
    """GET /admin/training-data — export interactions for optimizer."""
    conn = get_reallog()._get_connection()
    rows = conn.execute(
        """SELECT user_input, route_action, route_reason, target_model, 
                  response, feedback, response_latency_ms
           FROM interactions WHERE feedback IS NOT NULL
           ORDER BY created_at DESC LIMIT 1000"""
    ).fetchall()
    return JSONResponse([dict(r) for r in rows])
```

### L4. `common_non_names` set rebuilt per call

**File:** `vault/core.py`, line ~240

The large set literal is constructed every time `detect_entities()` runs. Should be a class attribute or module constant.

**Fix:**

```python
# Module level
_COMMON_NON_NAMES = frozenset({'Email', 'Send', ...})
```

### L5. `serve_index` returns JSONResponse for HTML

**File:** `gateway/routes.py`, line ~40

Should use `HTMLResponse` from starlette, not `JSONResponse`. The HTML gets JSON-escaped, breaking the UI.

**Fix:**

```python
from starlette.responses import HTMLResponse
# ...
return HTMLResponse(_index_html)
```

### L6. Schema duplication between `core.py` and `reallog_db.py`

Two different schema definitions exist:
- `core.py` `_init_db()` — the one actually used
- `reallog_db.py` `RealLogDB` — migration system, appears unused

The `reallog_db.py` has `pii_entities` table while `core.py` has `pii_map`. Column names differ (`entity_id` vs `entity_id`, `real_value` vs `real_value`). This will cause confusion.

**Fix:** Delete `reallog_db.py` or unify schemas. The migration system in `reallog_db.py` is good practice — consider adopting it for `core.py`'s `_init_db`.

### L7. Missing index on `interactions.created_at`

**File:** `vault/core.py`, `_init_db()`

No index on `created_at` for the interactions table, which is the natural sort order for any timeline/history query.

**Fix:**

```sql
CREATE INDEX IF NOT EXISTS idx_interactions_created ON interactions(created_at);
```

### L8. `_authenticate` called redundantly with `get_reallog`

**File:** `gateway/routes.py`, `_authenticate()` calls `get_reallog()` to get the JWT secret. Then every route handler calls `get_reallog()` again. Minor — singletons make this cheap — but could be cleaner:

```python
def _authenticate(request: Request) -> tuple[JSONResponse | None, RealLog]:
    reallog = get_reallog()
    # ... use reallog for secret lookup
    return None, reallog  # pass reallog through
```

---

## Summary Table

| ID | Impact | Area | Fix Effort |
|----|--------|------|------------|
| H1 | HIGH | httpx pooling | 15 min |
| H2 | HIGH | Regex compilation | 15 min |
| H3 | HIGH | SQLite WAL mode | 5 min |
| H4 | HIGH | Dehydrator/Rehydrator singletons | 10 min |
| H5 | HIGH | Entity ID generation | 30 min |
| H6 | HIGH | Rate limiting | 30 min |
| M1 | MED | Thread safety | 5 min |
| M2 | MED | Connection strategy | 30 min |
| M3 | MED | Timing metrics | 20 min |
| M4 | MED | serve_index caching + wrong type | 10 min |
| M5 | MED | Sessions tier query | 20 min |
| M6 | MED | Routing regex precompile | 10 min |
| M7 | MED | Draft concurrency cap | 15 min |
| M8 | MED | Health endpoint | 10 min |
| L1 | LOW | Rust rewrite analysis | N/A (not needed) |
| L2 | LOW | Jetson GPU monitoring | 20 min |
| L3 | LOW | Training data export | 15 min |
| L4 | LOW | common_non_names optimization | 5 min |
| L5 | LOW | serve_index response type | 2 min |
| L6 | LOW | Schema duplication | 30 min |
| L7 | LOW | Missing index | 2 min |
| L8 | LOW | Auth helper refactor | 10 min |

**Estimated total: ~5 hours for HIGH + MEDIUM fixes.**
