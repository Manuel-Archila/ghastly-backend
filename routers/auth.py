from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from config import Settings, get_settings
from core.errors import ForbiddenError
from core.rate_limit import LOGIN_RATE_LIMIT, limiter
from core.response import ApiResponse, ok
from dependencies import get_current_user, get_db
from schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LogoutRequest,
    MeUpdateRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserOut,
)
from services import auth_service
from storage.models.user import User

router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/register")
async def register(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiResponse[UserOut]:
    if not settings.allow_registration:
        raise ForbiddenError("El registro está deshabilitado.", code="REGISTRATION_DISABLED")
    user = await auth_service.register_user(db, payload)
    return ok(UserOut.model_validate(user), "Cuenta creada.")


@router.post("/login")
@limiter.limit(LOGIN_RATE_LIMIT)
async def login(
    request: Request,
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiResponse[TokenResponse]:
    tokens = await auth_service.login(db, settings, payload)
    return ok(tokens, "Sesión iniciada.")


@router.post("/refresh")
async def refresh(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> ApiResponse[TokenResponse]:
    tokens = await auth_service.refresh(db, settings, payload.refresh_token)
    return ok(tokens, "Token renovado.")


@router.post("/logout")
async def logout(
    payload: LogoutRequest,
    db: AsyncSession = Depends(get_db),
    _current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await auth_service.logout(db, payload.refresh_token)
    return ok(None, "Sesión cerrada.")


@router.get("/me")
async def me(current_user: User = Depends(get_current_user)) -> ApiResponse[UserOut]:
    return ok(UserOut.model_validate(current_user))


@router.patch("/me")
async def update_me(
    payload: MeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[UserOut]:
    updated = await auth_service.update_me(db, current_user, payload)
    return ok(UserOut.model_validate(updated), "Perfil actualizado.")


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApiResponse[None]:
    await auth_service.change_password(db, current_user, payload)
    return ok(None, "Contraseña actualizada.")
