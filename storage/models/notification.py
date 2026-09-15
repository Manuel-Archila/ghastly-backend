from __future__ import annotations

import uuid
from datetime import datetime
from datetime import time as time_

from sqlalchemy import BigInteger, DateTime, ForeignKey, SmallInteger, String, Time, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from storage.models.base import Base


class BudgetAlertSent(Base):
    """Dedup de `services.push_service.notify_budget_alert` (§ CLAUDE.md
    "check_budget_alerts"). `UNIQUE(user_id, category_id, month, threshold)`
    es lo que evita mandar el mismo aviso dos veces en el mes, sin importar
    si vino del gancho tras cada escritura o del job de las 20:00."""

    __tablename__ = "budget_alerts_sent"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    category_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    month: Mapped[str] = mapped_column(String(7), nullable=False)
    threshold: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class NotificationPreferences(Base):
    """Singleton por usuario (PLAN-backend.md §5) — reemplaza los defaults
    que `services/push_service.py` traía hardcodeados: umbrales de
    presupuesto, días de aviso de cuotas, horas de silencio y canales.
    No sincroniza al cliente (se edita vía REST directo, como el perfil de
    `users`), por eso sin `deleted_at`/`server_seq`."""

    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True
    )
    budget_alert_thresholds: Mapped[list[int]] = mapped_column(
        ARRAY(SmallInteger), nullable=False, server_default="{80,100}"
    )
    due_reminder_days: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")
    quiet_hours_start: Mapped[time_ | None] = mapped_column(Time)
    quiet_hours_end: Mapped[time_ | None] = mapped_column(Time)
    channels: Mapped[list[str]] = mapped_column(
        ARRAY(String(20)), nullable=False, server_default="{push}"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
