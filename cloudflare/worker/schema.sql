-- LOG-mcp Vault D1 Schema
CREATE TABLE IF NOT EXISTS pii_entities (
  id TEXT PRIMARY KEY,
  entity_type TEXT NOT NULL,
  real_value TEXT NOT NULL,
  placeholder TEXT NOT NULL,
  session_id TEXT,
  created_at TEXT NOT NULL,
  last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  provider TEXT,
  model TEXT,
  created_at TEXT NOT NULL,
  message_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS request_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  timestamp TEXT NOT NULL,
  session_id TEXT,
  provider TEXT,
  status INTEGER,
  input_tokens INTEGER,
  output_tokens INTEGER,
  error TEXT
);

CREATE INDEX IF NOT EXISTS idx_pii_session ON pii_entities(session_id);
CREATE INDEX IF NOT EXISTS idx_pii_type ON pii_entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_request_log_ts ON request_log(timestamp);
