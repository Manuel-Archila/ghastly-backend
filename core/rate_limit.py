"""Rate limiting (slowapi) — PLAN-backend §10.

`/auth/login`: 5/min por IP. Escritura general: 60/min por usuario.
`/sync/push`: 10/min (llega en Fase 1).
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

LOGIN_RATE_LIMIT = "5/minute"
WRITE_RATE_LIMIT = "60/minute"
