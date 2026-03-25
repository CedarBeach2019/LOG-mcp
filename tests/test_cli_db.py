"""Tests for vault CLI and reallog_db modules."""

import pytest
import tempfile
import os
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestReallogDB:
    """Test reallog_db schema and migration functionality."""

    def test_import(self):
        from vault import reallog_db
        assert hasattr(reallog_db, 'RealLogDB')
        assert hasattr(reallog_db, 'init_database')

    def test_init_creates_tables(self):
        from vault.reallog_db import init_database
        import sqlite3
        db = tempfile.mktemp(suffix='.db')
        try:
            init_database(db)
            conn = sqlite3.connect(db)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t[0] for t in tables]
            assert 'sessions' in table_names
            assert 'messages' in table_names
            assert 'pii_entities' in table_names
            conn.close()
        finally:
            os.unlink(db)

    def test_init_idempotent(self):
        """Running init twice should not fail."""
        from vault.reallog_db import init_database
        db = tempfile.mktemp(suffix='.db')
        try:
            init_database(db)
            init_database(db)  # Should not raise
        finally:
            os.unlink(db)

    def test_check_connection(self):
        from vault.reallog_db import init_database
        db = tempfile.mktemp(suffix='.db')
        try:
            init_database(db)
            from vault.reallog_db import RealLogDB
            rl = RealLogDB(db)
            assert rl.check_connection()
        finally:
            os.unlink(db)


class TestCLI:
    """Test CLI commands."""

    def _cli(self, *args, input_text=None):
        """Helper to run CLI command."""
        cmd = [sys.executable, str(Path(__file__).parent.parent / 'vault' / 'cli.py')] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            input=input_text,
            timeout=10,
            cwd=str(Path(__file__).parent.parent),
        )
        return result

    def test_init(self):
        result = self._cli('init')
        assert result.returncode == 0
        assert 'initialized' in result.stdout.lower() or 'exists' in result.stdout.lower()

    def test_dehydrate_pipe(self):
        result = self._cli('dehydrate', input_text='Email fresh-test@unique-cli.com and call 555-123-4567')
        assert result.returncode == 0
        assert '[EMAIL_' in result.stdout
        assert '[PHONE_' in result.stdout
        assert 'fresh-test@unique-cli.com' not in result.stdout

    def test_dehydrate_json(self):
        result = self._cli('dehydrate', '--json', input_text='Email test@cli.com SSN 000-00-0001')
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert 'entities' in data or 'dehydrated' in data

    def test_dehydrate_args(self):
        result = self._cli('dehydrate', 'Email test@cli.com')
        assert result.returncode == 0
        assert 'test@cli.com' not in result.stdout

    def test_rehydrate_pipe(self):
        result = self._cli('rehydrate', input_text='Contact [EMAIL_1> at [PHONE_1>')
        assert result.returncode == 0

    def test_rehydrate_args(self):
        result = self._cli('rehydrate', 'Send to [EMAIL_1>')
        assert result.returncode == 0

    def test_status(self):
        result = self._cli('status')
        assert result.returncode == 0
        assert 'L.O.G' in result.stdout or 'Vault' in result.stdout or 'entities' in result.stdout

    def test_gnosis(self):
        result = self._cli('gnosis', 'Test Title', 'Test body content')
        assert result.returncode == 0

    def test_entities_list(self):
        result = self._cli('entities', 'list')
        assert result.returncode == 0

    def test_no_input_still_works(self):
        """Dehydrate with no input should handle gracefully."""
        result = self._cli('dehydrate', input_text='')
        # Either error or empty result — should not crash
        assert result.returncode == 0 or 'No text' in result.stderr

    def test_version_or_help(self):
        result = self._cli('--help')
        assert result.returncode == 0
        assert 'dehydrate' in result.stdout or 'rehydrate' in result.stdout
