#!/bin/bash
# LOG-mcp quick setup
# Run this after cloning the repo

set -e
echo "🔒 LOG-mcp Setup"
echo "================"

# Check for Docker
if command -v docker &> /dev/null; then
    echo "✅ Docker found"
    USE_DOCKER=true
else
    echo "⚠️  Docker not found, will use Python"
    USE_DOCKER=false
fi

# Copy env file
if [ ! -f docker/.env ]; then
    cp docker/.env.example docker/.env
    echo "✅ Created docker/.env — edit API key before starting"
fi

if [ "$USE_DOCKER" = true ]; then
    # Check if .env has the default key
    if grep -q "sk-your-api-key" docker/.env; then
        echo ""
        echo "⚠️  ACTION NEEDED: Edit docker/.env and set your LOG_API_KEY"
        echo "   nano docker/.env"
        echo ""
        echo "Then start with:"
        echo "   cd docker && docker compose up"
    else
        echo "✅ API key configured"
        echo ""
        echo "Start with:"
        echo "   cd docker && docker compose up"
        echo ""
        echo "Then open: http://localhost:8000"
    fi
else
    # Python install
    echo "Installing Python dependencies..."
    pip install -e ".[dev]" 2>/dev/null || pip install -r requirements.txt
    echo "✅ Installed"

    echo ""
    echo "Set your API key:"
    echo "   export LOG_API_KEY=sk-your-key-here"
    echo ""
    echo "Start with:"
    echo "   uvicorn gateway.server:app --host 0.0.0.0 --port 8000"
    echo ""
    echo "Then open: http://localhost:8000"
fi

echo ""
echo "📚 Docs: docs/VISION.md, docs/PHASE2-PLAN.md"
echo "💬 Commands: /local /cloud /reason /compare /draft"
