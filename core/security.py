"""Hash de contraseñas (Argon2id) y JWT de acceso/refresco (PyJWT)."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from config import Settings

_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


@dataclass(frozen=True, slots=True)
class TokenPayload:
    user_id: UUID
    token_type: TokenType
    device_id: UUID | None
    expires_at: datetime


def create_access_token(user_id: UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_access_expires_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(
    user_id: UUID, device_id: UUID, settings: Settings
) -> tuple[str, str, datetime]:
    """Devuelve `(token, token_hash, expires_at)`.

    Solo el hash se guarda en `refresh_tokens.token_hash` — el token en
    claro nunca toca la DB, igual que una contraseña.
    """
    now = datetime.now(UTC)
    expires_at = now + timedelta(days=settings.jwt_refresh_expires_days)
    payload: dict[str, Any] = {
        "sub": str(user_id),
        "type": "refresh",
        "device_id": str(device_id),
        "jti": secrets.token_urlsafe(32),
        "iat": now,
        "exp": expires_at,
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return token, hash_refresh_token(token), expires_at


def hash_refresh_token(token: str) -> str:
    # sha256 alcanza: el token ya tiene alta entropía (es un JWT firmado);
    # esto es solo para no guardarlo en claro, no para resistir fuerza bruta.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def decode_token(token: str, settings: Settings, *, expected_type: TokenType) -> TokenPayload:
    payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(f"se esperaba un token de tipo {expected_type}")
    device_id = payload.get("device_id")
    return TokenPayload(
        user_id=UUID(payload["sub"]),
        token_type=payload["type"],
        device_id=UUID(device_id) if device_id else None,
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
    )
