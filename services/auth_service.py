"""Orquestación de auth: DB + `core/security.py`. Sin matemática de dinero aquí."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import jwt
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings
from core.errors import ConflictError, ForbiddenError, NotFoundError, UnauthorizedError
from core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from schemas.auth import (
    ChangePasswordRequest,
    DeviceUpsertRequest,
    LoginRequest,
    MeUpdateRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from storage.models.auth import Device, RefreshToken
from storage.models.user import User


async def register_user(db: AsyncSession, data: RegisterRequest) -> User:
    user = User(
        email=data.email.lower(),
        password_hash=hash_password(data.password),
        name=data.name,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError(
            "Ya existe una cuenta con ese correo.", field="email", code="EMAIL_TAKEN"
        ) from exc
    await db.commit()
    await db.refresh(user)
    return user


async def _upsert_device(db: AsyncSession, user_id: UUID, data: DeviceUpsertRequest) -> Device:
    device = await db.get(Device, data.id)
    if device is None:
        device = Device(id=data.id, user_id=user_id, platform=data.platform)
        db.add(device)
    elif device.user_id != user_id:
        raise ForbiddenError(
            "Ese dispositivo pertenece a otra cuenta.", code="DEVICE_OWNED_BY_OTHER"
        )

    device.platform = data.platform
    device.app_version = data.app_version
    if data.push_token is not None:
        device.push_token = data.push_token
    device.last_seen_at = datetime.now(UTC)
    await db.flush()
    return device


async def _issue_tokens(
    db: AsyncSession, settings: Settings, user: User, device_id: UUID
) -> tuple[str, str, int]:
    access_token = create_access_token(user.id, settings)
    refresh_token, token_hash, expires_at = create_refresh_token(user.id, device_id, settings)
    db.add(
        RefreshToken(
            user_id=user.id,
            device_id=device_id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
    )
    await db.flush()
    return access_token, refresh_token, settings.jwt_access_expires_minutes * 60


async def login(db: AsyncSession, settings: Settings, data: LoginRequest) -> TokenResponse:
    result = await db.execute(select(User).where(User.email == data.email.lower()))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(data.password, user.password_hash):
        raise UnauthorizedError("Correo o contraseña incorrectos.", code="INVALID_CREDENTIALS")

    await _upsert_device(
        db,
        user.id,
        DeviceUpsertRequest(
            id=data.device_id,
            platform=data.platform,
            app_version=data.app_version,
            push_token=data.push_token,
        ),
    )
    access_token, refresh_token, expires_in = await _issue_tokens(
        db, settings, user, data.device_id
    )
    await db.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
    )


async def refresh(db: AsyncSession, settings: Settings, refresh_token: str) -> TokenResponse:
    try:
        payload = decode_token(refresh_token, settings, expected_type="refresh")
    except jwt.InvalidTokenError as exc:
        raise UnauthorizedError("Refresh token inválido.", code="INVALID_REFRESH_TOKEN") from exc

    token_hash = hash_refresh_token(refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()

    if stored is None:
        # El JWT es válido pero no está en la tabla: o expiró y fue purgado, o
        # ya fue rotado antes. Tratamos como reuso y revocamos la familia
        # completa del dispositivo por seguridad (PLAN-backend §10).
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == payload.user_id)
            .where(RefreshToken.device_id == payload.device_id)
            .values(revoked_at=datetime.now(UTC))
        )
        await db.commit()
        raise UnauthorizedError(
            "Refresh token reusado o expirado. Se cerró la sesión del dispositivo.",
            code="REFRESH_REUSE_DETECTED",
        )

    if stored.revoked_at is not None:
        # Reuso confirmado de un token ya rotado: revoca toda la familia.
        await db.execute(
            update(RefreshToken)
            .where(RefreshToken.user_id == stored.user_id)
            .where(RefreshToken.device_id == stored.device_id)
            .values(revoked_at=datetime.now(UTC))
        )
        await db.commit()
        raise UnauthorizedError(
            "Refresh token reusado. Se cerró la sesión del dispositivo.",
            code="REFRESH_REUSE_DETECTED",
        )

    user = await db.get(User, stored.user_id)
    if user is None:
        raise UnauthorizedError("El usuario ya no existe.", code="USER_NOT_FOUND")

    stored.revoked_at = datetime.now(UTC)
    stored.last_used_at = datetime.now(UTC)
    access_token, new_refresh_token, expires_in = await _issue_tokens(
        db, settings, user, stored.device_id
    )
    await db.commit()
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_token,
        expires_in=expires_in,
        user=UserOut.model_validate(user),
    )


async def logout(db: AsyncSession, refresh_token: str) -> None:
    token_hash = hash_refresh_token(refresh_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    stored = result.scalar_one_or_none()
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(UTC)
    await db.commit()


async def change_password(db: AsyncSession, user: User, data: ChangePasswordRequest) -> None:
    if not verify_password(data.current_password, user.password_hash):
        raise UnauthorizedError("La contraseña actual no es correcta.", code="INVALID_CREDENTIALS")

    user.password_hash = hash_password(data.new_password)

    keep_hash = (
        hash_refresh_token(data.current_refresh_token) if data.current_refresh_token else None
    )
    stmt = (
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id)
        .where(RefreshToken.revoked_at.is_(None))
    )
    if keep_hash is not None:
        stmt = stmt.where(RefreshToken.token_hash != keep_hash)
    await db.execute(stmt.values(revoked_at=datetime.now(UTC)))
    await db.commit()


async def update_me(db: AsyncSession, user: User, data: MeUpdateRequest) -> User:
    for field in ("name", "base_currency", "timezone", "locale"):
        value = getattr(data, field)
        if value is not None:
            setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    return user


async def register_device(db: AsyncSession, user_id: UUID, data: DeviceUpsertRequest) -> Device:
    device = await _upsert_device(db, user_id, data)
    await db.commit()
    return device


async def delete_device(db: AsyncSession, user_id: UUID, device_id: UUID) -> None:
    device = await db.get(Device, device_id)
    if device is None or device.user_id != user_id:
        raise NotFoundError("El dispositivo no existe.", code="DEVICE_NOT_FOUND")
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.device_id == device_id)
        .values(revoked_at=datetime.now(UTC))
    )
    await db.delete(device)
    await db.commit()
