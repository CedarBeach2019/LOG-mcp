FROM nvcr.io/nvidia/l4t-jetpack:r36.3.0

# Install Python and dependencies
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY .. /app

RUN pip3 install --break-system-packages -e .

EXPOSE 8420

CMD ["python3", "mcp/server.py"]
