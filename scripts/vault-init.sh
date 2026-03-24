#!/bin/bash
# L.O.G. Vault Initialization Script
# Sets up directory structure and initializes the RealLog database

set -e

VAULT_DIR="$HOME/.log/vault"
ARCHIVE_DIR="$VAULT_DIR/archives"

echo "🪵 Initializing L.O.G. Vault..."
echo ""

# Create directory structure
mkdir -p "$VAULT_DIR"
mkdir -p "$ARCHIVE_DIR/shorts"
mkdir -p "$ARCHIVE_DIR/sessions"
mkdir -p "$ARCHIVE_DIR/gnosis"

echo "✅ Created directory structure:"
echo "   $VAULT_DIR/"
echo "   $ARCHIVE_DIR/{shorts,sessions,gnosis}"
echo ""

# Initialize RealLog database via Python
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_ROOT="$(dirname "$SCRIPT_DIR")"

if command -v python3 &> /dev/null; then
    export PYTHONPATH="$LOG_ROOT:$PYTHONPATH"
    python3 -c "
from vault.core import RealLog
log = RealLog('$VAULT_DIR/reallog.db')
stats = log.get_storage_stats()
print(f'✅ RealLog database initialized at {log.db_path}')
print(f'📊 {stats[\"entities\"]} entities, {stats[\"sessions\"]} sessions')
"
else
    echo "⚠️  Python 3 not found — install Python 3.10+ or use Docker"
fi

echo ""
echo "🪵 Vault is ready. Next steps:"
echo "   pip install -e $LOG_ROOT    # Install CLI"
echo "   log status                   # Check status"
echo "   log dehydrate 'test text'    # Test dehydration"
