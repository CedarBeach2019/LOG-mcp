"""Database migration system for LOG-mcp.

Tracks schema version and applies migrations incrementally.
Current schema is v1. This module provides the framework for future migrations.
"""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger("vault.migrations")

CODE_SCHEMA_VERSION = 1


def get_current_version(conn: sqlite3.Connection) -> int:
    """Get the current schema version from the DB. Returns 0 if not tracked."""
    # Ensure the table exists first
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER NOT NULL,
            applied_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    row = conn.execute("SELECT MAX(version) as v FROM schema_version").fetchone()
    return row[0] if row and row[0] is not None else 0


def run_migrations(conn: sqlite3.Connection) -> int:
    """Run all pending migrations. Returns the new schema version."""
    current = get_current_version(conn)

    if current >= CODE_SCHEMA_VERSION:
        logger.debug("Schema is up to date (v%d)", current)
        return current

    migrations = [
        # Future migrations go here:
        # (2, _migrate_v1_to_v2),
        # (3, _migrate_v2_to_v3),
    ]

    for target_version, migration_fn in migrations:
        if current < target_version <= CODE_SCHEMA_VERSION:
            try:
                conn.execute("BEGIN")
                migration_fn(conn)
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (target_version,),
                )
                conn.execute("COMMIT")
                logger.info("Migrated schema to v%d", target_version)
                current = target_version
            except Exception:
                conn.execute("ROLLBACK")
                logger.error("Migration to v%d failed, rolled back", target_version)
                raise

    # If at version 0 and no migrations ran, this is a fresh install at v1
    if current == 0 and CODE_SCHEMA_VERSION >= 1:
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version) VALUES (?)",
            (CODE_SCHEMA_VERSION,),
        )
        conn.commit()
        current = CODE_SCHEMA_VERSION

    logger.info("Schema is now at v%d", current)
    return current


def run_migrations_on_reallog(reallog) -> int:
    """Convenience: run migrations on a RealLog instance."""
    conn = reallog._get_connection()
    return run_migrations(conn)
