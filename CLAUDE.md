# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Ghastly — API de finanzas personales (Guatemala, GTQ). Backend de la app móvil en `../ghastly-frontend`.

**Documentos de referencia (leer antes de planear trabajo grande):**
- `PLAN-backend.md` — arquitectura, modelo de datos, catálogo completo de endpoints, fases.
- `PLAN-finanzas-app.md` — plan de producto y los 12 casos de negocio que no se pueden romper.

---

## Estado actual

**Fases 0-3 completas, Fase 4 en curso.** Fundaciones, núcleo transaccional (cuentas, categorías, transacciones, transferencias, reembolsos, sync), presupuestos, y los tres motores de Fase 3 (recurrentes/suscripciones, cuotas, deudas, metas) + jobs de APScheduler — todo implementado y probado (204 tests de dominio + API contra Postgres real). `/sync/push` maneja `account`, `category`, `transaction`, `budget`, `budget_item`. El consumo de presupuesto (`budget_service`) resta los reembolsos de su categoría original — caso 5 — tanto en el mes vivo como en el gasto congelado al cerrar el período. Los comandos de abajo ya funcionan tal cual. De Fase 4 ya está `GET /reports/dashboard` (patrimonio neto, flujo del mes, top 5 categorías, presupuesto global, próximos vencimientos, pasivo en cuotas; "por cobrar" es un stub `null` hasta Fase 5). Falta el resto de `/reports/*`, anomalías y tasa de ahorro.

Pendientes conocidos de Fase 3: entrega push real (los jobs de aviso dejan rastro en logs, no envían), el gancho de `check_budget_alerts` "tras cada escritura", `detect_anomalies` (es Fase 4), y `refresh_fx_rates` (no-op hasta decidir fuente de tasas).

---

## Comandos

```bash
uv sync --extra dev              # instalar dependencias
docker compose up -d postgres    # DB local
pyway migrate                    # aplicar migraciones
uv run fastapi dev main.py --host 0.0.0.0 --port 8011   # servidor con reload → http://localhost:8011/docs (LAN para el teléfono)

uv run pytest                    # todos los tests
uv run pytest tests/domain -q    # solo dominio (rápido, sin DB) — el ciclo normal
uv run pytest tests/api/test_transactions.py::test_transfer_is_atomic   # un solo test
uv run pytest -k "case_06"       # los tests de un caso de negocio

uv run ruff check . --fix
uv run ruff format .
uv run mypy .
```

Los tests de `tests/api/` levantan Postgres con testcontainers; requieren Docker corriendo. Los de `tests/domain/` no tocan nada externo y son los que se corren en cada cambio.

---

## Arquitectura

```
routers/     HTTP puro: valida, llama al service, responde. Sin DB, sin matemática.
services/    Orquestación: transacción de DB + llamadas a domain/.
domain/      ⚠️ Funciones puras. Sin FastAPI, sin SQLAlchemy, sin red. Aquí vive el negocio.
storage/     Modelos SQLAlchemy y sesión.
schemas/     Pydantic in/out.
jobs/        Tareas programadas (APScheduler).
migrations/  SQL versionado (pyway).
```

**Reglas de dependencia:**
- `domain/` no importa nada del resto. Si un cálculo necesita la DB para existir, está mal modelado.
- Un router nunca toca `AsyncSession` para consultar; delega en el service.
- Ninguna operación de dinero ocurre fuera de `domain/money.py`.

---

## Convenciones no negociables

| Tema | Regla |
|---|---|
| **Dinero** | `BIGINT` en centavos, campos `*_cents`. Nunca `float`, nunca `NUMERIC`, ni en logs. Toda aritmética pasa por `domain/money.py`. En repartos, el residuo lo absorbe la última fracción. |
| **Fechas** | Se almacena UTC (`TIMESTAMPTZ`); los campos de calendario son `DATE`. La zona de negocio es `America/Guatemala` y **la define el servidor**, no el dispositivo. |
| **IDs** | UUIDv7 **generados por el cliente**. El servidor nunca asigna IDs de entidades de dominio (es lo que hace idempotente la sincronización). |
| **Aislamiento** | Toda tabla de dominio tiene `user_id`; **toda query filtra por él en el service**, no se confía en el router. Cada endpoint con `{id}` necesita test de acceso cruzado. |
| **Respuestas** | Envelope `ApiResponse{is_success, message, data}`. `message` en español y apto para mostrar; los errores llevan `data.code` estable en inglés. El cliente decide por `code`, nunca por `message`. |
| **Borrado** | Lógico (`deleted_at`) en transacciones, archivado (`is_archived`) en cuentas y categorías. No existe borrado físico en la API. |
| **Paginación** | Siempre por cursor (`?limit=&cursor=` → `items[]` + `next_cursor`). Nunca offset. |
| **Idempotencia** | Todo `POST` que cree dinero acepta `Idempotency-Key`. Reintentar jamás duplica. |
| **Versionado** | Prefijo `/v1`. Un cambio incompatible abre `/v2`; `/v1` no se rompe. |
| **Migraciones** | pyway, `V<major>_<minor>__nombre.sql`. **Una migración aplicada nunca se edita**: se escribe la siguiente. |

---

## Reglas de negocio críticas

Están detalladas en `PLAN-backend.md` §6 con su implementación. Las que más se rompen al escribir código nuevo:

1. **Las transferencias no son gasto ni ingreso.** Todo query de reporte pasa por el helper `exclude_transfers()`. Si escribes un reporte nuevo y no lo usas, el reporte está mal.
2. **El pago de tarjeta es una transferencia.** Un gasto contra una cuenta `credit_card` que parece pago se rechaza con `USE_TRANSFER_FOR_CARD_PAYMENT`.
3. **Una compra a cuotas no es gasto del mes.** Solo la cuota corriente impacta el presupuesto; el pasivo total se reporta aparte.
4. **El monto en USD se congela.** `fx_rate` y `base_amount_cents` se guardan al crear y jamás se recalculan.
5. **Un reembolso resta de la categoría original**, no suma a ingresos.
6. **Los saldos son derivados.** `current_balance_cents` es caché; se recalcula en la misma transacción de DB que escribe el movimiento. Nunca se edita a mano — para eso está `POST /accounts/{id}/adjust`.
7. **Advertir, no bloquear.** Un posible duplicado se crea igual y devuelve `data.warning`.

Cada caso tiene test con su número: `tests/domain/test_case_06_installments_do_not_hit_budget.py`.

---

## Sincronización

Log de cambios con cursor monotónico (`server_seq BIGSERIAL` por usuario). El cliente hace `push` y luego `pull`, nunca al revés. Conflictos:

- Campos editables por el usuario → last-write-wins por `client_updated_at`.
- Campos derivados (saldos, consumo de presupuesto) → **siempre gana el servidor**; el cliente no los envía.
- Delete vs. update → gana el delete (es reversible).

Detalle en `PLAN-backend.md` §7. Un cliente que estuvo offline dos semanas debe poder empujar 2000 mutaciones sin romper nada.

---

## Cómo trabajar aquí

- **Orden dentro de cada tarea, sin excepción:** `domain` + sus tests → migración + modelos → service → schemas → router → test de API. No se avanza de capa con tests rojos.
- Endpoint nuevo = schema de entrada y salida, filtro por `user_id`, y test de acceso cruzado. Los tres.
- Antes de instalar una dependencia, evaluar si son 40 líneas propias.
- No introducir Celery/Redis mientras APScheduler alcance.
- Los jobs son idempotentes: se identifican por `(rule_id, occurrence_date)` y correr dos veces el mismo día no duplica nada.
- Sin PII en logs: nunca montos ni descripciones; sí IDs y `request_id`.
- Commits pequeños y descriptivos.
