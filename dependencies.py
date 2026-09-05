from __future__ import annotations

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings, get_settings
from core.errors import UnauthorizedError
from core.security import decode_token
from storage.db import get_db as get_db
from storage.models.user import User

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> User:
    if credentials is None:
        raise UnauthorizedError("Se requiere autenticación.", code="MISSING_TOKEN")
    try:
        payload = decode_token(credentials.credentials, settings, expected_type="access")
    except jwt.ExpiredSignatureError as exc:
        raise UnauthorizedError("El token expiró.", code="TOKEN_EXPIRED") from exc
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Token inválido.", code="INVALID_TOKEN") from exc

    user = await db.get(User, payload.user_id)
    if user is None:
        raise UnauthorizedError("El usuario ya no existe.", code="USER_NOT_FOUND")
    return user


__all__ = ["get_db", "get_current_user"]
