"""JWT authentication helpers."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from vault.core import RealLog

ALGORITHM = "HS256"
TOKEN_EXPIRY_HOURS = 24


def get_jwt_secret(reallog: RealLog) -> str:
    """Fetch or generate-and-persist the JWT signing secret."""
    import os
    import logging
    _logger = logging.getLogger("gateway.auth")

    # Priority 1: environment variable
    env_secret = os.environ.get("LOG_JWT_SECRET")
    if env_secret:
        return env_secret

    # Priority 2: DB-stored secret
    conn = reallog._get_connection()
    row = conn.execute(
        "SELECT value FROM user_preferences WHERE key = ?",
        ("jwt_secret",),
    ).fetchone()
    if row:
        return row["value"]

    # No secret anywhere — generate and persist (first run)
    _logger.warning("JWT secret not found in env (LOG_JWT_SECRET) or DB — generating new one")
    secret = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO user_preferences (key, value) VALUES (?, ?)",
        ("jwt_secret", secret),
    )
    conn.commit()
    return secret


def create_token(secret: str, *, expiry_hours: int = TOKEN_EXPIRY_HOURS) -> str:
    """Create a JWT with the given secret and 24-hour expiry."""
    now = datetime.now(timezone.utc)
    payload = {
        "iat": now,
        "exp": now + timedelta(hours=expiry_hours),
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def verify_token(token: str, secret: str) -> dict | None:
    """Return the decoded payload or *None* if the token is invalid/expired."""
    try:
        return jwt.decode(token, secret, algorithms=[ALGORITHM])
    except JWTError:
        return None
