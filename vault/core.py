"""
L.O.G. Vault - Local privacy engine.

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
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
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

    def __init__(self, db_path: str | Path = "~/.log/vault/reallog.db", settings=None):
        if settings is not None:
            self.db_path = Path(settings.db_path).expanduser()
        else:
            self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = __import__('threading').Lock()
        self.settings = settings
        # Create a persistent connection
        self._conn = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create a persistent database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # Enable WAL mode for concurrent reads and foreign keys
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys = ON")
        return self._conn

    @property
    def db(self) -> sqlite3.Connection:
        """Convenience alias for _get_connection()."""
        return self._get_connection()

    def close(self):
        """Close the persistent connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _init_db(self):
        """Initialize database tables."""
        conn = self._get_connection()
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
            CREATE TABLE IF NOT EXISTS user_preferences (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS interactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_input TEXT NOT NULL,
                rewritten_input TEXT,
                route_action TEXT NOT NULL,
                route_reason TEXT,
                target_model TEXT NOT NULL,
                response TEXT NOT NULL,
                escalation_response TEXT,
                response_latency_ms INTEGER,
                escalation_latency_ms INTEGER,
                feedback TEXT DEFAULT NULL,
                critique TEXT DEFAULT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_interactions_route ON interactions(route_action);
            CREATE INDEX IF NOT EXISTS idx_interactions_feedback ON interactions(feedback);
        """)
        conn.commit()

    def add_session(self, session: Session) -> None:
        """Add a new session."""
        conn = self._get_connection()
        conn.execute(
            "INSERT INTO sessions (id, timestamp, summary, metadata) VALUES (?, ?, ?, ?)",
            (session.id, session.timestamp, session.summary, json.dumps(session.metadata))
        )
        conn.commit()

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
        """Generate next log ID for an entity type. For compatibility."""
        # This method is kept for compatibility, but Dehydrator uses _next_letter_id
        # We'll generate a similar ID using the same logic
        type_prefix = {
            'person': 'PERSON',
            'email': 'EMAIL',
            'phone': 'PHONE',
            'address': 'ADDRESS',
            'ssn': 'SSN',
            'credit_card': 'CC',
            'api_key': 'KEY',
            'passport': 'PASSPORT'
        }.get(entity_type, 'ENTITY')
        
        # Generate ID directly without creating a Dehydrator (to avoid circular issues)
        prefix = type_prefix.upper()
        conn = self._get_connection()
        rows = conn.execute(
            "SELECT entity_id FROM pii_map WHERE entity_id LIKE ?",
            (f"{prefix}_%",)
        ).fetchall()
        used_ids = set()
        for row in rows:
            eid = row[0]
            used_ids.add(eid)
        
        # Try single letters A-Z first
        for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            candidate = f"{prefix}_{c}"
            if candidate not in used_ids:
                return candidate
        
        # Try two-letter combinations
        for c1 in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            for c2 in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                candidate = f"{prefix}_{c1}{c2}"
                if candidate not in used_ids:
                    return candidate
        
        # Fallback: use prefix with timestamp
        import time
        return f"{prefix}_{int(time.time())}"


    # -- Interaction tracking (Phase 2) --

    def add_interaction(self, session_id: str, user_input: str, route_action: str,
                       target_model: str, response: str, route_reason: str = "",
                       rewritten_input: str = None, response_latency_ms: int = 0,
                       escalation_response: str = None,
                       escalation_latency_ms: int = None) -> int:
        """Store a completed interaction for feedback tracking."""
        conn = self._get_connection()
        cursor = conn.execute(
            """INSERT INTO interactions
               (session_id, user_input, rewritten_input, route_action, route_reason,
                target_model, response, escalation_response,
                response_latency_ms, escalation_latency_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, user_input, rewritten_input, route_action, route_reason,
             target_model, response, escalation_response,
             response_latency_ms, escalation_latency_ms)
        )
        conn.commit()
        return cursor.lastrowid

    def update_feedback(self, interaction_id: int, feedback: str,
                        critique: str = None) -> bool:
        """Update feedback (up/down) and optional critique for an interaction."""
        conn = self._get_connection()
        if critique:
            conn.execute(
                "UPDATE interactions SET feedback = ?, critique = ? WHERE id = ?",
                (feedback, critique, interaction_id)
            )
        else:
            conn.execute(
                "UPDATE interactions SET feedback = ? WHERE id = ?",
                (feedback, interaction_id)
            )
        conn.commit()
        return conn.total_changes > 0

    def get_interaction(self, interaction_id: int):
        """Get a single interaction by ID."""
        conn = self._get_connection()
        return conn.execute(
            "SELECT * FROM interactions WHERE id = ?", (interaction_id,)
        ).fetchone()

    def get_preferences(self) -> dict:
        """Get all user preferences as a dict."""
        conn = self._get_connection()
        rows = conn.execute("SELECT key, value FROM user_preferences").fetchall()
        return {row["key"]: row["value"] for row in rows}

    def set_preference(self, key: str, value: str):
        """Upsert a user preference."""
        conn = self._get_connection()
        conn.execute(
            """INSERT INTO user_preferences (key, value, updated_at)
               VALUES (?, ?, datetime('now'))
               ON CONFLICT(key) DO UPDATE SET value = excluded.value,
               updated_at = datetime('now')""",
            (key, value)
        )
        conn.commit()

    def delete_preference(self, key: str) -> bool:
        """Delete a user preference. Returns True if it existed."""
        conn = self._get_connection()
        cursor = conn.execute(
            "DELETE FROM user_preferences WHERE key = ?", (key,)
        )
        conn.commit()
        return cursor.rowcount > 0

    def _seed_default_preferences(self):
        """Insert default preferences if they don't exist."""
        defaults = {
            "response_style": "concise",
            "no_disclaimers": "true",
            "tone": "casual",
            "show_work": "false",
            "format": "bullet_points",
        }
        conn = self._get_connection()
        for key, value in defaults.items():
            existing = conn.execute(
                "SELECT 1 FROM user_preferences WHERE key = ?", (key,)
            ).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO user_preferences (key, value) VALUES (?, ?)",
                    (key, value)
                )
        conn.commit()


class Dehydrator:
    """Detect and replace PII with typed bracket placeholders."""

    # Class-level compiled patterns (shared across all instances — no per-request recompilation)
    _COMPILED_PATTERNS = {k: re.compile(v, re.IGNORECASE if k == 'address' else 0)
                          for k, v in {
        'email': r'\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b',
        'phone': r'\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b',
        'ssn': r'\b\d{3}[-.]?\d{2}[-.]?\d{4}\b',
        'credit_card': r'\b(?:\d{4}[- ]?){3}\d{4}\b',
        'api_key': r'\b(?:sk|pk|token|key|secret|api[_-]?key)[-_][a-zA-Z0-9_\-]{16,}\b',
    }.items()}

    def __init__(self, reallog: RealLog, settings=None):
        self.reallog = reallog
        self.settings = settings
        self.patterns = self._COMPILED_PATTERNS

    @staticmethod
    def build_preamble() -> str:
        """Build the coherence preamble explaining entity tokens to the LLM."""
        return (
            "Some personal information in this conversation has been replaced with tokens for privacy. "
            "Tokens follow the format [TYPE_LETTER]. For example, [PERSON_A] refers to a specific "
            "person whose name is not provided. [EMAIL_B] refers to a specific email address. "
            "Treat each token as a unique, consistent entity throughout the conversation - the same "
            "token always refers to the same real entity. Respond naturally as if you know who these "
            "entities are, but never try to guess or fabricate their real values."
        )

    def detect_entities(self, text: str) -> List[Tuple[str, str]]:
        entities = []

        for entity_type, pattern in self.patterns.items():
            for match in pattern.finditer(text):
                entities.append((entity_type, match.group()))

        # Improved name detection
        # First pass: find all 2-3 word sequences of capitalized words
        common_non_names = {
            'Email', 'Send', 'Contact', 'Call', 'The', 'This', 'That', 'There',
            'Hello', 'Please', 'Thank', 'Hi', 'Hey', 'To', 'From', 'Subject',
            'Re', 'FW', 'Fwd', 'Attn', 'Attention', 'Dear', 'Regards', 'Sincerely',
            'Best', 'Kind', 'Yours', 'Cordially', 'Respectfully', 'Also', 'Just',
            'Then', 'Will', 'Would', 'Could', 'Should', 'About', 'After', 'Before',
            'With', 'When', 'What', 'Where', 'Which', 'Have', 'Here', 'Some',
            'Other', 'More', 'Only', 'Over', 'Into', 'Very', 'Much', 'Many',
            'Such', 'Each', 'Every', 'Both', 'Few', 'Most', 'Than', 'Them',
            'These', 'Those', 'Being', 'Made', 'Does', 'Did', 'How', 'Our',
            'Your', 'Their', 'His', 'Her', 'My', 'Its', 'We', 'They', 'You',
            'Not', 'But', 'And', 'Or', 'Nor', 'For', 'Yet', 'So',
            'Account', 'Bank', 'Card', 'Case', 'Chapter', 'Company', 'Conference',
            'Country', 'Department', 'Division', 'Document', 'Employee', 'Employer',
            'Employment', 'Group', 'Insurance', 'Message', 'Note', 'Number',
            'Office', 'Order', 'Page', 'Patient', 'Payment', 'Phone', 'Project',
            'Record', 'Reference', 'Report', 'Request', 'Section', 'Server',
            'State', 'Statement', 'System', 'Team', 'Ticket', 'Total', 'Transaction',
            'Unit', 'User', 'Vault', 'Version', 'Week', 'World', 'Work',
            'Ask', 'Tell', 'Give', 'Show', 'Bring', 'Take', 'Make', 'Find',
            'Know', 'Think', 'Want', 'Need', 'Help', 'Try', 'Use', 'See',
        }
        titles = {'Mr', 'Mrs', 'Ms', 'Miss', 'Dr', 'Prof', 'Sir', 'Madam'}

        # Match sequences of 2-3 capitalized words
        name_pattern = r'(?<![A-Za-z])([A-Z][a-z]{1,15})(?:\s+([A-Z][a-z]{1,15})){1,2}(?![A-Za-z])'
        for match in re.finditer(name_pattern, text):
            words = match.group().split()

            # Skip if ALL words are in common_non_names or titles
            clean_words = [w for w in words if w.rstrip('.') not in common_non_names and w.rstrip('.') not in titles]
            if len(clean_words) >= 2:
                clean_name = ' '.join(clean_words)
                # Skip if remaining words are too common
                if not any(w in common_non_names for w in clean_words):
                    entities.append(('person', clean_name))
                elif len(clean_words) >= 2:
                    # At least 2 non-common words - likely a real name
                    entities.append(('person', clean_name))

        # Address detection (simple)
        address_pattern = r'\b\d+\s+[A-Z][a-z]+\s+(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Boulevard|Blvd|Drive|Dr|Court|Ct)\b'
        for match in re.finditer(address_pattern, text, re.IGNORECASE):
            entities.append(('address', match.group()))

        # Passport number detection
        passport_patterns = [
            r'\b[A-Z][0-9]{8}\b',  # Standard format
            r'\b[A-Z]{1,2}[0-9]{6,8}\b'  # Variant formats
        ]
        for pattern in passport_patterns:
            for match in re.finditer(pattern, text):
                entities.append(('passport', match.group()))

        # Non-ASCII support
        # Chinese phone numbers
        chinese_phone_pattern = r'\b1[3-9]\d{9}\b'
        for match in re.finditer(chinese_phone_pattern, text):
            entities.append(('phone', match.group()))

        # Russian names (Cyrillic)
        russian_name_pattern = r'\b[А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?\b'
        for match in re.finditer(russian_name_pattern, text):
            entities.append(('person', match.group()))

        # Chinese names (2-3 Han characters)
        chinese_name_pattern = r'[\u4e00-\u9fff]{2,3}'
        # To avoid matching random Chinese characters, we'll look for patterns that suggest names
        # This is a simple approach - in practice, more context would be needed
        # We'll look for sequences of 2-3 Han characters that are standalone or preceded/followed by common markers
        chinese_context_pattern = r'(?:姓名|名字|称呼|为)[::]?\s*([\u4e00-\u9fff]{2,3})|([\u4e00-\u9fff]{2,3})(?:先生|女士|小姐|老师)'
        for match in re.finditer(chinese_context_pattern, text):
            for group_num in range(1, 3):
                if match.group(group_num):
                    entities.append(('person', match.group(group_num)))
                    break

        return entities

    def dehydrate(self, text: str, *, use_llm: bool = False) -> Tuple[str, List[PIIEntity]]:
        """Replace detected PII with LOG_ID placeholders.

        Args:
            text: Input text to scrub.
            use_llm: If True, also query a local Ollama model to catch
                PII that regex misses (relationships, implicit references).
                Defaults to False for speed.
        """
        entities = self.detect_entities(text)

        # Optional LLM pass for contextual PII
        if use_llm:
            try:
                from vault.llm_scorer import score_pii_sync

                llm_result = score_pii_sync(text)
                for ent in llm_result.get("entities", []):
                    ent_text = ent.get("text", "").strip()
                    ent_type = self._map_llm_type(ent.get("type", "other"))
                    if ent_text:
                        entities.append((ent_type, ent_text))
            except Exception:
                logger.debug("LLM scorer failed, falling back to regex-only", exc_info=True)

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
            result = result.replace(real_value, f"[{entity_id}]")

        return result, processed_entities

    @staticmethod
    def _map_llm_type(llm_type: str) -> str:
        """Map LLM-detected entity types to our internal types."""
        mapping = {
            "person": "person",
            "relationship": "person",
            "location": "address",
            "other": "person",
        }
        return mapping.get(llm_type.lower(), "person")

    def _get_entity_by_value(self, real_value: str) -> Optional[PIIEntity]:
        """Retrieve entity by its real value."""
        conn = self.reallog._get_connection()
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
        """Generate a unique entity ID using letter+counter (e.g., EMAIL_A, PERSON_B)."""
        type_prefix = {
            'person': 'PERSON',
            'email': 'EMAIL',
            'phone': 'PHONE',
            'address': 'ADDRESS',
            'ssn': 'SSN',
            'credit_card': 'CC',
            'api_key': 'KEY',
            'passport': 'PASSPORT'
        }.get(entity_type, 'ENTITY')
        return self._next_letter_id(type_prefix)

    def _next_letter_id(self, prefix: str) -> str:
        """Get next letter-suffixed ID for a prefix (A, B, ..., Z, AA, AB, ...).
        
        Uses MAX-based lookup instead of fetching all rows — O(1) query.
        """
        prefix = prefix.upper()
        conn = self.reallog._get_connection()
        row = conn.execute(
            "SELECT entity_id FROM pii_map WHERE entity_id LIKE ? ORDER BY entity_id DESC LIMIT 1",
            (f"{prefix}_%",)
        ).fetchone()
        
        if row is None:
            return f"{prefix}_A"
        
        # Parse the last ID and increment
        suffix = row[0].split("_", 1)[1]
        # Convert to number: A=0, B=1, ..., Z=25, AA=26, AB=27, ...
        def to_num(s):
            return sum((ord(c) - 65) * (26 ** i) for i, c in enumerate(reversed(s)))
        def to_letters(n):
            if n < 26:
                return chr(65 + n)
            chars = []
            while n >= 26:
                chars.append(chr(65 + n % 26))
                n = n // 26 - 1
            chars.append(chr(65 + n))
            return "".join(reversed(chars))
        
        next_num = to_num(suffix) + 1
        return f"{prefix}_{to_letters(next_num)}"

    def _store_entity(self, entity: PIIEntity) -> PIIEntity:
        """Thread-safe check-and-insert for PII entities."""
        with self.reallog._lock:
            # Use the RealLog's connection directly
            conn = self.reallog._get_connection()
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
                conn.commit()
                return existing

            # Generate ID
            entity_type = entity.entity_type or 'ENTITY'
            entity_id = self._next_letter_id(entity_type)
            entity.entity_id = entity_id

            conn.execute(
                "INSERT INTO pii_map (entity_id, entity_type, real_value, created_at, last_used) VALUES (?, ?, ?, ?, ?)",
                (entity.entity_id, entity.entity_type, entity.real_value, entity.created_at, entity.last_used)
            )
            conn.commit()
            return entity

    def _update_last_used(self, entity_id: str) -> None:
        """Update the last_used timestamp for an entity."""
        conn = self.reallog._get_connection()
        conn.execute(
            "UPDATE pii_map SET last_used = ? WHERE entity_id = ?",
            (datetime.now().isoformat(), entity_id)
        )
        conn.commit()


class Rehydrator:
    """Swap LOG_ID placeholders back to real values."""

    def __init__(self, reallog: RealLog):
        self.reallog = reallog

    def rehydrate(self, text: str) -> str:
        """Replace all bracket placeholders with their real values."""
        # Find all placeholders in the text: [EMAIL_A], [PERSON_B], etc.
        placeholders = re.findall(r'\[([A-Z]+_[A-Z]+)\]', text)
        result = text

        for placeholder in placeholders:
            entity = self._get_entity_by_id(placeholder)
            if entity:
                result = result.replace(f"[{placeholder}]", entity.real_value)
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


# ---------------------------------------------------------------------------
# Interaction tracking (Phase 2)
# ---------------------------------------------------------------------------
