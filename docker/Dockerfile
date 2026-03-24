# ── Stage 1: Dependencies ──────────────────────────────────────────
FROM python:3.12-slim AS deps
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Stage 2: Runtime ───────────────────────────────────────────────
FROM python:3.12-slim
WORKDIR /app

COPY --from=deps /usr/local/lib/python3.12/site-packages/ /usr/local/lib/python3.12/site-packages/
COPY --from=deps /usr/local/bin/ /usr/local/bin/

COPY vault/ ./vault/
COPY mcp/ ./mcp/
COPY scouts/ ./scouts/
COPY tests/ ./tests/
COPY pyproject.toml setup.py ./

# Install the package itself
RUN pip install --no-cache-dir -e .

# Data volume
VOLUME ["/data"]

EXPOSE 8000

ENV VAULT_DB_PATH=/data/reallog.db
ENV PYTHONUNBUFFERED=1

# Health check — run CLI status
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
  CMD python -m vault.cli status || exit 1

CMD ["sh", "-c", "python -m vault.cli init && tail -f /dev/null"]
# NOTE: MCP server uses stdio transport by default.
# For HTTP transport, use: python -c "from mcp.server import *; run_http(host='0.0.0.0', port=8000)"
# Or connect via: docker exec -it <container> log dehydrate "text here"
