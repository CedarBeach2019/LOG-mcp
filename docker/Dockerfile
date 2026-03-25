FROM python:3.12-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY vault/ vault/
COPY gateway/ gateway/
COPY web/ web/

# Create vault data directory
RUN mkdir -p /root/.log/vault

# Expose gateway port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/')" || exit 1

# Start the gateway
CMD ["uvicorn", "gateway.server:app", "--host", "0.0.0.0", "--port", "8000"]
