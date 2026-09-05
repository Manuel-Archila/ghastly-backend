from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    device_id: UUID
    platform: str = Field(min_length=1, max_length=20)
    app_version: str | None = None
    push_token: str | None = None


class RefreshRequest(BaseModel):
    refresh_token: str


class LogoutRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)
    # Si se envía, ese refresh token sobrevive a la revocación masiva
    # (para no desloguear el dispositivo desde el que se cambia la contraseña).
    current_refresh_token: str | None = None


class MeUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    base_currency: str | None = Field(default=None, min_length=3, max_length=3)
    timezone: str | None = None
    locale: str | None = None


class UserOut(BaseModel):
    id: UUID
    email: str
    name: str
    base_currency: str
    timezone: str
    locale: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class DeviceUpsertRequest(BaseModel):
    id: UUID
    platform: str = Field(min_length=1, max_length=20)
    app_version: str | None = None
    push_token: str | None = None


class DeviceOut(BaseModel):
    id: UUID
    platform: str
    app_version: str | None
    push_token: str | None
    last_sync_seq: int
    last_seen_at: datetime | None

    model_config = {"from_attributes": True}
