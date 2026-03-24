"""
L.O.G. Vault — Local privacy engine.

Core dehydration/rehydration logic with SQLite-backed RealLog.
"""

from __future__ import annotations
import json
import sqlite3
import re
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Iterator, List, Tuple, Dict, Any
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MemoryTier(Enum):
    HOT = "hot"
    WARM = "warm"
    COLD = "cold"
    ICE = "ice"


@dataclass
class PIIEntity:
    """A single PII entity in the RealLog."""
    entity_id: str          # e.g., ENTITY_1, EMAIL_3
    entity_type: str        # person, email, phone, address, ssn, credit_card, api_key
    real_value: str
    created_at: str = ""
    last_used: str = ""
    
    @property
    def log_id(self) -> str:
        """Alias for entity_id for backward compatibility."""
        return self.entity_id


@dataclass
class Session:
    """A single session."""
    id: str
    timestamp: str
    summary: str
    metadata: dict = field(default_factory=dict)


@dataclass
class Message:
    """A single message in a session."""
    id: int
    session_id: str
    role: str
    content: str
    timestamp: str


class DatabaseConnection:
    """Context manager for SQLite connections with error handling."""
    
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.conn: Optional[sqlite3.Connection] = None
    
    def __enter__(self) -> sqlite3.Connection:
        try:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
            return self.conn
        except sqlite3.Error as e:
            logger.error(f"Database connection error: {e}")
            raise
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.conn:
            if exc_type is not None:
                self.conn.rollback()
            else:
                self.conn.commit()
            self.conn.close()


class RealLog:
    """SQLite-backed storage for sessions, messages, and PII mappings."""
    
    def __init__(self, db_path: str | Path = "~/.log/vault/reallog.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = __import__('threading').Lock()
        self._init_db()
    
    def _init_db(self):
        """Initialize database tables."""
        with DatabaseConnection(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS pii_map (
                    entity_id TEXT PRIMARY KEY,
                    entity_type TEXT NOT NULL,
                    real_value TEXT NOT NULL,
                    created_at TEXT DEFAULT (datetime('now')),
                    last_used TEXT DEFAULT (datetime('now'))
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
                CREATE INDEX IF NOT EXISTS idx_pii_type ON pii_map(entity_type);
                CREATE INDEX IF NOT EXISTS idx_pii_value ON pii_map(real_value);
            """)
    
    def add_session(self, session: Session) -> None:
        """Add a new session."""
        with DatabaseConnection(self.db_path) as conn:
            conn.execute(
                "INSERT INTO sessions (id, timestamp, summary, metadata) VALUES (?, ?, ?, ?)",
                (session.id, session.timestamp, session.summary, json.dumps(session.metadata))
            )
    
    def add_message(self, message: Message) -> int:
        """Add a message and return its id."""
        with DatabaseConnection(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (message.session_id, message.role, message.content, message.timestamp)
            )
            return cursor.lastrowid
    
    def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieve a session by id."""
        with DatabaseConnection(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, timestamp, summary, metadata FROM sessions WHERE id = ?",
                (session_id,)
            ).fetchone()
            if row:
                return Session(
                    id=row['id'],
                    timestamp=row['timestamp'],
                    summary=row['summary'],
                    metadata=json.loads(row['metadata'])
                )
        return None
    
    def get_session_messages(self, session_id: str) -> List[Message]:
        """Get all messages for a session."""
        with DatabaseConnection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, session_id, role, content, timestamp FROM messages WHERE session_id = ? ORDER BY timestamp",
                (session_id,)
            ).fetchall()
            return [
                Message(
                    id=row['id'],
                    session_id=row['session_id'],
                    role=row['role'],
                    content=row['content'],
                    timestamp=row['timestamp']
                ) for row in rows
            ]
    
    def get_all_sessions(self, limit: int = 100) -> List[Session]:
        """Get all sessions ordered by timestamp."""
        with DatabaseConnection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, timestamp, summary, metadata FROM sessions ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [
                Session(
                    id=row['id'],
                    timestamp=row['timestamp'],
                    summary=row['summary'],
                    metadata=json.loads(row['metadata'])
                ) for row in rows
            ]
    
    # Add missing methods for compatibility
    def get_storage_stats(self) -> dict:
        """Get storage statistics."""
        with DatabaseConnection(self.db_path) as conn:
            entities = conn.execute("SELECT COUNT(*) FROM pii_map").fetchone()[0]
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            
            return {
                "entities": entities,
                "sessions": sessions,
                "messages": messages,
                "archives": 0,  # Placeholder
                "by_tier": {"hot": sessions, "warm": 0, "cold": 0, "ice": 0}
            }
    
    def db_size_mb(self) -> float:
        """Get database size in MB."""
        if self.db_path.exists():
            return self.db_path.stat().st_size / (1024 * 1024)
        return 0.0
    
    def promote_session(self, session_id: str, tier: MemoryTier) -> None:
        """Promote a session to a different memory tier."""
        # Update session metadata to include tier information
        session = self.get_session(session_id)
        if session:
            metadata = session.metadata
            metadata['tier'] = tier.value
            with DatabaseConnection(self.db_path) as conn:
                conn.execute(
                    "UPDATE sessions SET metadata = ? WHERE id = ?",
                    (json.dumps(metadata), session_id)
                )
    
    def get_sessions(self, tier: MemoryTier, limit: int = 100) -> List[Session]:
        """Get sessions by tier."""
        sessions = []
        with DatabaseConnection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, timestamp, summary, metadata FROM sessions ORDER BY timestamp DESC LIMIT ?",
                (limit,)
            ).fetchall()
            for row in rows:
                metadata = json.loads(row['metadata'])
                if metadata.get('tier') == tier.value:
                    sessions.append(Session(
                        id=row['id'],
                        timestamp=row['timestamp'],
                        summary=row['summary'],
                        metadata=metadata
                    ))
        return sessions
    
    def all_entities(self):
        """Get all PII entities."""
        with DatabaseConnection(self.db_path) as conn:
            rows = conn.execute(
                "SELECT entity_id, entity_type, real_value, created_at, last_used FROM pii_map"
            ).fetchall()
            return [
                PIIEntity(
                    entity_id=row['entity_id'],
                    entity_type=row['entity_type'],
                    real_value=row['real_value'],
                    created_at=row['created_at'],
                    last_used=row['last_used']
                ) for row in rows
            ]
    
    def register_entity(self, entity: PIIEntity) -> None:
        """Register a PII entity."""
        with DatabaseConnection(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO pii_map (entity_id, entity_type, real_value, created_at, last_used) VALUES (?, ?, ?, ?, ?)",
                (entity.entity_id, entity.entity_type, entity.real_value, entity.created_at, entity.last_used)
            )
    
    def next_log_id(self, entity_type: str) -> str:
        """Generate next log ID for an entity type."""
        with DatabaseConnection(self.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM pii_map WHERE entity_type = ?",
                (entity_type,)
            ).fetchone()[0]
        
        type_prefix = {
            'person': 'ENTITY',
            'email': 'EMAIL',
            'phone': 'PHONE',
            'address': 'ADDR',
            'ssn': 'SSN',
            'credit_card': 'CC',
            'api_key': 'KEY'
        }.get(entity_type, 'ENT')
        
        return f"{type_prefix}_{count + 1}"


class Dehydrator:
    """Detect and replace PII with LOG_ID placeholders."""
    
    def __init__(self, reallog: RealLog):
        self.reallog = reallog
        self.patterns = {
            'email': r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b',
            'phone': r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',
            'ssn': r'\b\d{3}[-.]?\d{2}[-.]?\d{4}\b',
            'credit_card': r'\b(?:\d{4}[- ]?){3}\d{4}\b',
            'api_key': r'\b(?:sk|pk|token|key|secret|api[_-]?key)[-_][a-zA-Z0-9_\-]{16,}\b',
        }
    
    def detect_entities(self, text: str) -> List[Tuple[str, str]]:
        """Detect PII entities in text using regex patterns."""
        entities = []
        
        for entity_type, pattern in self.patterns.items():
            for match in re.finditer(pattern, text, re.IGNORECASE):
                entities.append((entity_type, match.group()))
        
        # NLP heuristics for names (simple approach)
        # Look for title-case words that might be names
        name_pattern = r'\b(?:Mr\.|Mrs\.|Ms\.|Dr\.)?\s*[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b'
        for match in re.finditer(name_pattern, text):
            # Basic filter to exclude common non-name words
            common_words = {'The', 'This', 'That', 'There', 'Hello', 'Please', 'Thank'}
            if match.group() not in common_words:
                entities.append(('person', match.group()))
        
        # Address detection (simple)
        address_pattern = r'\b\d+\s+[A-Z][a-z]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln)\b'
        for match in re.finditer(address_pattern, text, re.IGNORECASE):
            entities.append(('address', match.group()))
        
        return entities
    
    def dehydrate(self, text: str) -> Tuple[str, List[PIIEntity]]:
        """Replace detected PII with LOG_ID placeholders."""
        entities = self.detect_entities(text)
        processed_entities = []
        result = text
        
        for entity_type, real_value in entities:
            # Check if entity already exists in database
            existing = self._get_entity_by_value(real_value)
            if existing:
                entity_id = existing.entity_id
                self._update_last_used(entity_id)
                processed_entities.append(existing)
            else:
                pii_entity = PIIEntity(
                    entity_id="",  # Will be set by _store_entity
                    entity_type=entity_type,
                    real_value=real_value,
                    created_at=datetime.now().isoformat(),
                    last_used=datetime.now().isoformat()
                )
                pii_entity = self._store_entity(pii_entity)
                entity_id = pii_entity.entity_id
                processed_entities.append(pii_entity)
            
            # Replace in text
            result = result.replace(real_value, f"<{entity_id}>")
        
        return result, processed_entities
    
    def _get_entity_by_value(self, real_value: str) -> Optional[PIIEntity]:
        """Retrieve entity by its real value."""
        with DatabaseConnection(self.reallog.db_path) as conn:
            row = conn.execute(
                "SELECT entity_id, entity_type, real_value, created_at, last_used FROM pii_map WHERE real_value = ?",
                (real_value,)
            ).fetchone()
            if row:
                return PIIEntity(
                    entity_id=row['entity_id'],
                    entity_type=row['entity_type'],
                    real_value=row['real_value'],
                    created_at=row['created_at'],
                    last_used=row['last_used']
                )
        return None
    
    def _generate_entity_id(self, entity_type: str) -> str:
        """Generate a unique entity ID using MAX+1 for thread safety."""
        type_prefix = {
            'person': 'ENTITY',
            'email': 'EMAIL',
            'phone': 'PHONE',
            'address': 'ADDR',
            'ssn': 'SSN',
            'credit_card': 'CC',
            'api_key': 'KEY'
        }.get(entity_type, 'ENT')
        
        with DatabaseConnection(self.reallog.db_path) as conn:
            row = conn.execute(
                "SELECT MAX(CAST(SUBSTR(entity_id, ?) AS INTEGER)) FROM pii_map WHERE entity_type = ?",
                (len(type_prefix) + 2, entity_type,)  # +2 for underscore position
            ).fetchone()[0]
            count = row if row is not None else 0
        
        return f"{type_prefix}_{count + 1}"
    
    def _store_entity(self, entity: PIIEntity) -> PIIEntity:
        """Thread-safe check-and-insert for PII entities."""
        with self.reallog._lock:
            with DatabaseConnection(self.reallog.db_path) as conn:
                # Check if already exists
                row = conn.execute(
                    "SELECT entity_id, entity_type, real_value, created_at, last_used FROM pii_map WHERE real_value = ?",
                    (entity.real_value,)
                ).fetchone()
                if row:
                    existing = PIIEntity(
                        entity_id=row['entity_id'],
                        entity_type=row['entity_type'],
                        real_value=row['real_value'],
                        created_at=row['created_at'],
                        last_used=row['last_used']
                    )
                    conn.execute(
                        "UPDATE pii_map SET last_used = ? WHERE entity_id = ?",
                        (datetime.now().isoformat(), existing.entity_id)
                    )
                    return existing
                
                # Generate ID
                type_prefix = {
                    'person': 'ENTITY', 'email': 'EMAIL', 'phone': 'PHONE',
                    'address': 'ADDR', 'ssn': 'SSN', 'credit_card': 'CC', 'api_key': 'KEY'
                }.get(entity.entity_type, 'ENT')
                
                row = conn.execute(
                    "SELECT MAX(CAST(SUBSTR(entity_id, ?) AS INTEGER)) FROM pii_map WHERE entity_type = ?",
                    (len(type_prefix) + 2, entity.entity_type,)
                ).fetchone()[0]
                count = row if row is not None else 0
                entity.entity_id = f"{type_prefix}_{count + 1}"
                
                conn.execute(
                    "INSERT INTO pii_map (entity_id, entity_type, real_value, created_at, last_used) VALUES (?, ?, ?, ?, ?)",
                    (entity.entity_id, entity.entity_type, entity.real_value, entity.created_at, entity.last_used)
                )
                return entity
    
    def _update_last_used(self, entity_id: str) -> None:
        """Update the last_used timestamp for an entity."""
        with DatabaseConnection(self.reallog.db_path) as conn:
            conn.execute(
                "UPDATE pii_map SET last_used = ? WHERE entity_id = ?",
                (datetime.now().isoformat(), entity_id)
            )


class Rehydrator:
    """Swap LOG_ID placeholders back to real values."""
    
    def __init__(self, reallog: RealLog):
        self.reallog = reallog
    
    def rehydrate(self, text: str) -> str:
        """Replace all LOG_ID placeholders with their real values."""
        # Find all placeholders in the text
        placeholders = re.findall(r'<([A-Z_]+_\d+)>', text)
        result = text
        
        for placeholder in placeholders:
            entity = self._get_entity_by_id(placeholder)
            if entity:
                result = result.replace(f"<{placeholder}>", entity.real_value)
                # Update last_used
                self._update_last_used(placeholder)
            else:
                logger.warning(f"Unknown entity ID: {placeholder}")
        
        return result
    
    def _get_entity_by_id(self, entity_id: str) -> Optional[PIIEntity]:
        """Retrieve entity by its ID."""
        with DatabaseConnection(self.reallog.db_path) as conn:
            row = conn.execute(
                "SELECT entity_id, entity_type, real_value, created_at, last_used FROM pii_map WHERE entity_id = ?",
                (entity_id,)
            ).fetchone()
            if row:
                return PIIEntity(
                    entity_id=row['entity_id'],
                    entity_type=row['entity_type'],
                    real_value=row['real_value'],
                    created_at=row['created_at'],
                    last_used=row['last_used']
                )
        return None
    
    def _update_last_used(self, entity_id: str) -> None:
        """Update the last_used timestamp for an entity."""
        with DatabaseConnection(self.reallog.db_path) as conn:
            conn.execute(
                "UPDATE pii_map SET last_used = ? WHERE entity_id = ?",
                (datetime.now().isoformat(), entity_id)
            )


# Utility functions
def create_session(session_id: str, summary: str, metadata: Optional[dict] = None) -> Session:
    """Create a new session."""
    return Session(
        id=session_id,
        timestamp=datetime.now().isoformat(),
        summary=summary,
        metadata=metadata or {}
    )


def create_message(session_id: str, role: str, content: str) -> Message:
    """Create a new message."""
    return Message(
        id=0,  # Will be set by database
        session_id=session_id,
        role=role,
        content=content,
        timestamp=datetime.now().isoformat()
    )
