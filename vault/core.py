"""
L.O.G. Vault — Local privacy engine.

Core dehydration/rehydration logic with SQLite-backed RealLog.
"""

from __future__ import annotations
import json
import sqlite3
import hashlib
import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


class MemoryTier(Enum):
    HOT = "hot"      # Local, immediate access
    WARM = "warm"    # Cloud vectors, searchable
    COLD = "cold"    # Archived, compressed
    ICE = "ice"      # Pruned — only Gnosis retained


@dataclass
class PIIEntity:
    """A single PII entity in the RealLog."""
    log_id: str          # e.g. ENTITY_1, EMAIL_3
    real_value: str
    entity_type: str     # person, email, phone, address, api_key, custom
    context: str = ""    # where it was found
    approved: bool = True
    created_at: str = ""


@dataclass
class ArchiveSession:
    """A single archived session."""
    session_id: str
    started_at: str
    ended_at: str
    summary: str
    tier: MemoryTier = MemoryTier.HOT
    topic: str = ""
    tags: list[str] = field(default_factory=list)
    message_count: int = 0
    token_estimate: int = 0


class RealLog:
    """SQLite-backed mapping of LOG_IDs to real values.

    This database NEVER leaves the Vault. It is the single source
    of truth for rehydration.
    """

    def __init__(self, db_path: str | Path = "~/.log/vault/reallog.db"):
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS entities (
                    log_id TEXT PRIMARY KEY,
                    real_value TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    context TEXT DEFAULT '',
                    approved INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT (datetime('now')),
                    last_seen TEXT DEFAULT (datetime('now'))
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    started_at TEXT,
                    ended_at TEXT,
                    summary TEXT,
                    topic TEXT,
                    tier TEXT DEFAULT 'hot',
                    tags TEXT DEFAULT '[]',
                    message_count INTEGER DEFAULT 0,
                    token_estimate INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS archives (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    stored_at TEXT DEFAULT (datetime('now')),
                    tier TEXT DEFAULT 'hot',
                    full_text_path TEXT,
                    summary_path TEXT,
                    gnosis TEXT DEFAULT '',
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
                CREATE INDEX IF NOT EXISTS idx_sessions_tier ON sessions(tier);
                CREATE INDEX IF NOT EXISTS idx_archives_tier ON archives(tier);
            """)

    # --- Entity CRUD ---

    def register_entity(self, entity: PIIEntity) -> PIIEntity:
        """Register a new PII mapping. Returns the (possibly existing) entity."""
        with sqlite3.connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT log_id, real_value FROM entities WHERE log_id = ?",
                (entity.log_id,)
            ).fetchone()
            if existing:
                return entity
            conn.execute(
                "INSERT INTO entities (log_id, real_value, entity_type, context, approved, created_at) VALUES (?, ?, ?, ?, ?, datetime('now'))",
                (entity.log_id, entity.real_value, entity.entity_type, entity.context, int(entity.approved))
            )
        return entity

    def get_entity(self, log_id: str) -> Optional[PIIEntity]:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT log_id, real_value, entity_type, context, approved, created_at FROM entities WHERE log_id = ?",
                (log_id,)
            ).fetchone()
            if row:
                return PIIEntity(log_id=row[0], real_value=row[1], entity_type=row[2],
                                 context=row[3], approved=bool(row[4]), created_at=row[5])
        return None

    def lookup_by_real_value(self, real_value: str) -> Optional[PIIEntity]:
        """Find existing entity by real value (exact match)."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT log_id, real_value, entity_type, context, approved, created_at FROM entities WHERE real_value = ?",
                (real_value,)
            ).fetchone()
            if row:
                return PIIEntity(log_id=row[0], real_value=row[1], entity_type=row[2],
                                 context=row[3], approved=bool(row[4]), created_at=row[5])
        return None

    def all_entities(self) -> list[PIIEntity]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT log_id, real_value, entity_type, context, approved, created_at FROM entities ORDER BY log_id"
            ).fetchall()
            return [PIIEntity(log_id=r[0], real_value=r[1], entity_type=r[2],
                              context=r[3], approved=bool(r[4]), created_at=r[5]) for r in rows]

    # --- Dehydration ---

    def next_log_id(self, entity_type: str) -> str:
        """Generate the next LOG_ID for a given entity type."""
        type_map = {
            "person": "ENTITY", "name": "ENTITY", "email": "EMAIL",
            "phone": "PHONE", "address": "ADDRESS", "api_key": "KEY",
            "secret": "SECRET", "url": "URL", "org": "ORG",
        }
        prefix = type_map.get(entity_type.lower(), "ENTITY")
        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM entities WHERE entity_type = ?",
                (entity_type.lower(),)
            ).fetchone()[0]
        return f"{prefix}_{count + 1}"

    def dehydrate(self, text: str, entities: list[PIIEntity] | None = None) -> tuple[str, list[PIIEntity]]:
        """Replace PII in text with LOG_IDs. Returns (dehydrated_text, entities).

        If entities are provided, uses them. Otherwise, runs regex detection.
        """
        if entities is None:
            entities = self._detect_entities(text)

        dehydrated = text
        for ent in entities:
            # Register if new
            existing = self.lookup_by_real_value(ent.real_value)
            if existing:
                ent.log_id = existing.log_id
            else:
                ent.log_id = self.next_log_id(ent.entity_type)
                self.register_entity(ent)
            dehydrated = dehydrated.replace(ent.real_value, f"<{ent.log_id}>")

        return dehydrated, entities

    def rehydrate(self, text: str) -> str:
        """Swap all LOG_ID placeholders back to real values."""
        entities = self.all_entities()
        result = text
        for ent in entities:
            result = result.replace(f"<{ent.log_id}>", ent.real_value)
        return result

    def _detect_entities(self, text: str) -> list[PIIEntity]:
        """Regex-based PII detection. First pass — local, no model needed."""
        entities = []

        # Emails
        for m in re.finditer(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text):
            entities.append(PIIEntity(log_id="", real_value=m.group(), entity_type="email"))

        # Phone numbers (US-focused, extensible)
        for m in re.finditer(r'\b\d{3}[-.]?\d{3}[-.]?\d{4}\b', text):
            entities.append(PIIEntity(log_id="", real_value=m.group(), entity_type="phone"))

        # API keys / tokens (heuristic)
        for m in re.finditer(r'(?:sk|pk|token|key|secret|api[_-]?key)[-_][a-zA-Z0-9_\-]{16,}', text, re.IGNORECASE):
            entities.append(PIIEntity(log_id="", real_value=m.group(), entity_type="api_key"))

        # URLs (potentially sensitive)
        for m in re.finditer(r'https?://[^\s<>"{}|\\^`]+', text):
            url = m.group()
            # Skip common public URLs
            public_domains = ('github.com', 'docs.openclaw.ai', 'clawhub.com', 'developers.cloudflare.com')
            if not any(d in url for d in public_domains):
                entities.append(PIIEntity(log_id="", real_value=url, entity_type="url"))

        return entities

    # --- Session Archiving ---

    def save_session(self, session: ArchiveSession):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sessions
                (session_id, started_at, ended_at, summary, topic, tier, tags, message_count, token_estimate)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session.session_id, session.started_at, session.ended_at,
                session.summary, session.topic, session.tier.value,
                json.dumps(session.tags), session.message_count, session.token_estimate
            ))

    def get_sessions(self, tier: MemoryTier | None = None, limit: int = 50) -> list[ArchiveSession]:
        with sqlite3.connect(self.db_path) as conn:
            if tier:
                rows = conn.execute(
                    "SELECT session_id, started_at, ended_at, summary, topic, tier, tags, message_count, token_estimate FROM sessions WHERE tier = ? ORDER BY started_at DESC LIMIT ?",
                    (tier.value, limit)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT session_id, started_at, ended_at, summary, topic, tier, tags, message_count, token_estimate FROM sessions ORDER BY started_at DESC LIMIT ?",
                    (limit,)
                ).fetchall()
            return [
                ArchiveSession(
                    session_id=r[0], started_at=r[1], ended_at=r[2],
                    summary=r[3], topic=r[4], tier=MemoryTier(r[5]),
                    tags=json.loads(r[6]) if r[6] else [], message_count=r[7],
                    token_estimate=r[8]
                ) for r in rows
            ]

    def archive_session(self, session_id: str, full_text_path: str, summary_path: str, tier: MemoryTier = MemoryTier.HOT):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO archives (session_id, stored_at, tier, full_text_path, summary_path)
                VALUES (?, datetime('now'), ?, ?, ?)
            """, (session_id, tier.value, full_text_path, summary_path))

    # --- Hysteresis Pruning ---

    def promote_session(self, session_id: str, new_tier: MemoryTier):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("UPDATE sessions SET tier = ? WHERE session_id = ?", (new_tier.value, session_id))
            conn.execute("UPDATE archives SET tier = ? WHERE session_id = ?", (new_tier.value, session_id))

    def get_storage_stats(self) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            entities = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            archives = conn.execute("SELECT COUNT(*) FROM archives").fetchone()[0]
            by_tier = {}
            for row in conn.execute("SELECT tier, COUNT(*) FROM sessions GROUP BY tier"):
                by_tier[row[0]] = row[1]
            return {"entities": entities, "sessions": sessions, "archives": archives, "by_tier": by_tier}

    def db_size_mb(self) -> float:
        if self.db_path.exists():
            return self.db_path.stat().st_size / (1024 * 1024)
        return 0.0
