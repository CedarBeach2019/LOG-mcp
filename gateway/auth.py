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
    row = reallog.db.execute(
        "SELECT value FROM user_preferences WHERE key = ?",
        ("jwt_secret",),
    ).fetchone()
    if row:
        return row["value"]

    secret = secrets.token_urlsafe(32)
    reallog.db.execute(
        "INSERT INTO user_preferences (key, value) VALUES (?, ?)",
        ("jwt_secret", secret),
    )
    reallog.db.commit()
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
