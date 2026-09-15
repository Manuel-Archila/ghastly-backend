from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, SmallInteger, String, func
from sqlalchemy.dialects.postgresql import UUID
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
