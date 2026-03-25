"""
Unified message store — single source of truth for conversation history.

Migrates from two tables (messages + interactions) to interactions-only.
The messages table becomes a view for backward compatibility.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("vault.unified_store")


def migrate_to_unified(db_path: str | Path) -> bool:
    """Migrate to unified message storage.

    1. Add session_id column to interactions if missing
    2. Copy any orphan messages into interactions
    3. Create messages view over interactions
    4. Drop old messages table
    5. Create indexes for fast session queries
    """
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    conn.execute("PRAGMA journal_mode=WAL")

    # Check if already migrated
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='view' AND name='messages'"
    ).fetchall()
    if rows:
        conn.close()
        return False  # already migrated

    try:
        # 1. Ensure session_id on interactions
        cols = [r[1] for r in conn.execute("PRAGMA table_info(interactions)").fetchall()]
        if "session_id" not in cols:
            conn.execute("ALTER TABLE interactions ADD COLUMN session_id TEXT DEFAULT ''")

        # 2. Copy orphan messages (messages without matching interactions)
        orphan_msgs = conn.execute("""
            SELECT m.session_id, m.role, m.content, m.timestamp
            FROM messages m
            LEFT JOIN interactions i ON i.session_id = m.session_id
                AND i.user_input = m.content AND i.user_input != ''
            WHERE i.id IS NULL AND m.role = 'user'
        """).fetchall()

        for msg in orphan_msgs:
            conn.execute("""
                INSERT INTO interactions (session_id, user_input, route_action, target_model, response, timestamp)
                VALUES (?, ?, 'MANUAL', '', '', ?)
            """, (msg[0], msg[2], msg[3]))

        orphan_assistant = conn.execute("""
            SELECT m.session_id, m.content, m.timestamp
            FROM messages m
            LEFT JOIN interactions i ON i.session_id = m.session_id
                AND i.response = m.content AND i.response != ''
            WHERE i.id IS NULL AND m.role = 'assistant'
        """).fetchall()

        for msg in orphan_assistant:
            conn.execute("""
                UPDATE interactions SET response = ? WHERE session_id = ? AND (response IS NULL OR response = '')
            """, (msg[1], msg[0]))

        # 3. Rename old messages table
        conn.execute("ALTER TABLE messages RENAME TO messages_old")

        # 4. Create messages view (union of user inputs + responses from interactions)
        conn.execute("""
            CREATE VIEW messages AS
            SELECT
                ROW_NUMBER() OVER (ORDER BY i.timestamp, i.id) as id,
                i.session_id,
                'user' as role,
                i.user_input as content,
                i.timestamp
            FROM interactions i
            WHERE i.user_input IS NOT NULL AND i.user_input != ''

            UNION ALL

            SELECT
                ROW_NUMBER() OVER (ORDER BY i.timestamp, i.id) as id,
                i.session_id,
                'assistant' as role,
                i.response as content,
                i.timestamp
            FROM interactions i
            WHERE i.response IS NOT NULL AND i.response != ''
        """)

        # 5. Create indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_interactions_session ON interactions(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_interactions_timestamp ON interactions(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_interactions_feedback ON interactions(feedback)")

        conn.commit()
        logger.info("Migrated to unified message store (%d orphan messages merged)",
                    len(orphan_msgs) + len(orphan_assistant))
        return True

    except Exception as exc:
        logger.warning("Migration failed (non-fatal): %s", exc)
        conn.rollback()
        return False
    finally:
        conn.close()
