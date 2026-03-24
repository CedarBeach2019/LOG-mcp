"""
SQLite database schema and migration support for RealLog.
"""
import sqlite3
import logging
from pathlib import Path
from typing import Optional, List, Tuple, Any, Dict

logger = logging.getLogger(__name__)

# Current database version
CURRENT_VERSION = 1

class RealLogDB:
    """Manage SQLite database schema and migrations."""
    
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
    def get_connection(self) -> sqlite3.Connection:
        """Get a database connection with proper settings."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_db(self) -> None:
        """Initialize the database with the current schema."""
        with self.get_connection() as conn:
            # Create version table if it doesn't exist
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_version (
                    version INTEGER PRIMARY KEY,
                    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Get current version
            version_result = conn.execute(
                "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
            ).fetchone()
            
            current_version = version_result[0] if version_result else 0
            
            # Run migrations
            for target_version in range(current_version + 1, CURRENT_VERSION + 1):
                logger.info(f"Running migration to version {target_version}")
                self._run_migration(conn, target_version)
                conn.execute(
                    "INSERT INTO schema_version (version) VALUES (?)",
                    (target_version,)
                )
                conn.commit()
                logger.info(f"Migration to version {target_version} completed")
    
    def _run_migration(self, conn: sqlite3.Connection, version: int) -> None:
        """Execute migration for a specific version."""
        if version == 1:
            # Create sessions table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    summary TEXT,
                    metadata TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Create messages table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE
                )
            """)
            
            # Create pii_entities table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pii_entities (
                    entity_id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    real_value TEXT NOT NULL,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    usage_count INTEGER DEFAULT 1
                )
            """)
            
            # Create indexes
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_messages_session_id 
                ON messages (session_id)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pii_entity_type 
                ON pii_entities (entity_type)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_pii_real_value 
                ON pii_entities (real_value)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_created 
                ON sessions (created_at)
            """)
            
    def check_connection(self) -> bool:
        """Verify database connection and basic functionality."""
        try:
            with self.get_connection() as conn:
                conn.execute("SELECT 1")
            return True
        except sqlite3.Error as e:
            logger.error(f"Database connection check failed: {e}")
            return False
    
    def vacuum(self) -> None:
        """Optimize database storage."""
        with self.get_connection() as conn:
            conn.execute("VACUUM")
    
    def backup(self, backup_path: str | Path) -> None:
        """Create a backup of the database."""
        backup_path = Path(backup_path).expanduser().resolve()
        with self.get_connection() as src:
            with sqlite3.connect(str(backup_path)) as dst:
                src.backup(dst)
        logger.info(f"Database backed up to {backup_path}")

def init_database(db_path: str | Path = "~/.log/vault/reallog.db") -> RealLogDB:
    """Initialize the database and return a RealLogDB instance."""
    db = RealLogDB(db_path)
    db.init_db()
    return db
