FROM nvcr.io/nvidia/l4t-jetpack:r36.3.0

# Install Python and system dependencies
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3-pip \
    python3.10-venv \
    sqlite3 \
    && rm -rf /var/lib/apt/lists/*

# Set up working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml .
COPY vault/ ./vault/
COPY mcp/ ./mcp/
COPY scouts/ ./scouts/

# Install Python dependencies
RUN pip3 install --break-system-packages --upgrade pip setuptools wheel
RUN pip3 install --break-system-packages -e .

# Create necessary directories
RUN mkdir -p /root/.log/vault/archives

# Expose MCP server port
EXPOSE 8420

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python3 -c "import socket; s = socket.socket(socket.AF_INET, socket.SOCK_STREAM); s.settimeout(2); result = s.connect_ex(('localhost',8420)); s.close(); exit(result)"

# Start the MCP server
CMD ["uvicorn", "mcp.server:app", "--host", "0.0.0.0", "--port", "8420", "--reload"]
