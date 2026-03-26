"""Tests for the database migration system."""

import sqlite3
import tempfile
import os

from vault.migrations import get_current_version, run_migrations, CODE_SCHEMA_VERSION


class TestMigrationVersion:
    def test_fresh_db_has_version_zero(self):
        """A brand-new DB should report version 0 before any migration."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            conn = sqlite3.connect(path)
            version = get_current_version(conn)
            assert version == 0
            conn.close()
        finally:
            os.unlink(path)

    def test_migration_creates_version_table(self):
        """Running migrations should create the schema_version table."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            conn = sqlite3.connect(path)
            run_migrations(conn)
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
            ).fetchone()
            assert row is not None
            conn.close()
        finally:
            os.unlink(path)

    def test_version_updated_after_migration(self):
        """After running migrations, version should match CODE_SCHEMA_VERSION."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            conn = sqlite3.connect(path)
            version = run_migrations(conn)
            assert version == CODE_SCHEMA_VERSION
            assert get_current_version(conn) == CODE_SCHEMA_VERSION
            conn.close()
        finally:
            os.unlink(path)

    def test_idempotent_migrations(self):
        """Running migrations twice should not error or change version."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            conn = sqlite3.connect(path)
            v1 = run_migrations(conn)
            v2 = run_migrations(conn)
            assert v1 == v2 == CODE_SCHEMA_VERSION
            conn.close()
        finally:
            os.unlink(path)

    def test_migration_on_reallog(self):
        """Integration: RealLog should run migrations on init."""
        from vault.core import RealLog
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            reallog = RealLog(db_path)
            try:
                conn = reallog._get_connection()
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
                ).fetchone()
                assert row is not None
            finally:
                reallog.close()
