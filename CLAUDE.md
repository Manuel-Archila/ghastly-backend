# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Ghastly — API de finanzas personales (Guatemala, GTQ). Backend de la app móvil en `../ghastly-frontend`.

**Documentos de referencia (leer antes de planear trabajo grande):**
- `PLAN-backend.md` — arquitectura, modelo de datos, catálogo completo de endpoints, fases.
- `PLAN-finanzas-app.md` — plan de producto y los 12 casos de negocio que no se pueden romper.

---

## Estado actual

**Fases 0-4 completas, Fase 5 en curso.** Fundaciones, núcleo transaccional (cuentas, categorías, transacciones, transferencias, reembolsos, sync), presupuestos, los tres motores de Fase 3 (recurrentes/suscripciones, cuotas, deudas, metas) + jobs de APScheduler, y los diez endpoints de `/reports/*` de Fase 4 — todo implementado y probado (338 tests de dominio + API contra Postgres real). `/sync/push` maneja `account`, `category`, `transaction`, `budget`, `budget_item`. El consumo de presupuesto (`budget_service`) resta los reembolsos de su categoría original — caso 5 — tanto en el mes vivo como en el gasto congelado al cerrar el período. Los comandos de abajo ya funcionan tal cual.

`/reports/*`: `dashboard` (patrimonio neto, flujo del mes, top 5 categorías, presupuesto global, próximos vencimientos, pasivo en cuotas, por cobrar), `by-category` (desglose de gasto o ingreso por categoría en cualquier rango, para la dona), `cashflow` (serie de ingreso/gasto por mes o semana, sin huecos), `expected-income` (las dos bases del caso 7 — mes anterior y promedio 3m — vía `budget_service.income_for_month`, público), `net-worth` (patrimonio neto histórico, reconstruido mes a mes con `domain.balances.compute_balance_series` sobre el ledger completo de cada cuenta y deuda — no un snapshot guardado), `trends` (misma serie de cashflow por mes + los dos promedios de ingreso del caso 12, con y sin extraordinarios), `comparison` (ingreso/gasto y desglose por categoría de dos meses `?a=&b=`), `anomalies` (gasto por categoría del mes contra el promedio de los 3 anteriores, umbral +30% sobre una base mínima de Q50 — `domain/anomalies.py`), `savings-rate` (% de ingreso no gastado por mes, reusa `cashflow`) y `upcoming` (calendario completo de cuotas + recurrentes + corte/pago de tarjeta + pago mensual de deudas activas, sin el recorte de 5 del dashboard).

**De Fase 5 (§12 del plan: export, backup cifrado, receivables, plantillas, recibos en S3) ya están `receivables`, `transaction_templates` y los recibos en S3.**

`receivables` — caso de negocio 3, gastos compartidos. `POST /receivables` liga un `Receivable` a un gasto ya registrado (`transaction_id`); puede haber varios contra la misma transacción si se repartió entre varias personas, validado para que la suma no supere el monto del gasto. `POST /receivables/{id}/settle` crea una transacción de INGRESO real (a diferencia del reembolso del caso 5, acá sí es ingreso — la plata entra de un tercero, no del comercio) ligada vía `transactions.receivable_id`, espejo de `refund_of_id`; pasa por `Idempotency-Key` porque crea dinero. El dashboard ya expone `receivable_cents` real (suma de los `pending`, aparte del patrimonio) — dejó de ser el stub `null`.

`transaction_templates` — plantillas de gasto frecuente (`GET/POST/DELETE /transaction-templates[/{id}]`, ordenadas por `use_count` desc). Son solo datos para prellenar `POST /transactions` en el cliente: no hay un `transactions.template_id` persistido (a diferencia de `refund_of_id`/`receivable_id`) porque la transacción resultante es una entidad plenamente independiente. `TransactionCreate` acepta un `template_id` opcional que **no se guarda** — solo dispara `transaction_template_service.mark_used` (incrementa `use_count`, actualiza `last_used_at`) dentro de la misma transacción de DB; si el `template_id` no existe o no es del usuario, la escritura completa se revierte (`TEMPLATE_NOT_FOUND`).

Recibos — `core/object_storage.py` es el cliente boto3 contra Cloudflare R2 (§14 "Decididas": compatible con la API de S3, solo cambia `endpoint_url`). `POST /transactions/{id}/receipt` firma una URL de subida (5 min, CLAUDE.md §10) y guarda `receipt_key` de forma **optimista** (antes de que el cliente confirme la subida — construir un callback de R2 es infraestructura de más para un solo usuario; si la subida falla, se vuelve a pedir). `GET /transactions/{id}/receipt` firma una de bajada — no está en la tabla abreviada de endpoints del plan (que solo lista el `POST`), pero hace falta para poder ver el recibo después, así que se agregó como complemento natural. La clave es determinística (`receipts/{user_id}/{transaction_id}`): volver a pedir upload para la misma transacción reemplaza el recibo, no acumula huérfanos. `generate_presigned_url` firma localmente (HMAC), no pega contra R2, así que los tests corren con credenciales de mentira sin mockear nada. Sin R2 configurado (`s3_bucket_receipts` vacío) responde `503 OBJECT_STORAGE_NOT_CONFIGURED`. De paso quedó expuesto en `TransactionOut` lo que faltaba: `receivable_id` y `receipt_key`.

Falta el resto de Fase 5: export y backup cifrado. Las decisiones de infraestructura que los bloqueaban ya están tomadas (PLAN-backend.md §14 "Decididas"): backup cifrado **reenviando la contraseña de login** en `POST /backup`/`POST /restore` (el servidor deriva la clave con Argon2id + salt propio del backup y la descarta, nunca la guarda), y el destino de despliegue **sigue sin decidir a propósito** — no bloquea nada porque todo viaja por variables de entorno.

**Huecos del catálogo del plan cerrados:** `GET /accounts/{id}/statement?cycle=current|previous|YYYY-MM` (consumo del corte, fecha de pago, saldo reconstruido al corte — no `current_balance_cents`, que es de HOY — y `minimum_cents` solo si la cuenta tiene `minimum_payment_percent` configurado; no se inventa un % "típico", varía por banco). `GET /transactions` ya soporta `q` (búsqueda en descripción/comercio/notas) y `tags` (overlap: cualquiera de los tags pedidos — ojo, la columna es `TEXT[]` y hay que castear el bind param explícito o Postgres no compara contra `VARCHAR[]`). `GET /info` (versión + última migración de pyway aplicada, autenticado — no está marcado 🔓 en el plan).

**Quedan fuera a propósito** (el usuario confirmó que no los necesita): FX manual (`GET /fx/rate`, `PUT /fx/rates`), `/export` y `/backup`/`/restore`.

El job `detect_anomalies` (día 1, 06:00) ya corre para todos los usuarios contra el mes que acaba de cerrar, reusando `report_service.get_anomalies` — deja el rastro en logs (`category_id`, `month`, `percent_increase`; nunca montos) igual que los demás avisos.

El gancho de `check_budget_alerts` "tras cada escritura" también quedó cerrado: `budget_service.check_alerts_for_category` se llama desde `transaction_service.create_transaction`/`update_transaction`/`restore_transaction` (dentro de la misma transacción de DB, ve el consumo ya actualizado) — el job de las 20:00 sigue corriendo aparte para cubrir presupuestos sin movimiento ese día. `bulk_categorize` y `refund` quedan fuera a propósito: un reembolso resta gasto y nunca cruza un umbral hacia arriba.

Pendientes conocidos: entrega push real (los jobs de aviso, incluido este gancho, dejan rastro en logs pero no envían nada — Fase 5) y `refresh_fx_rates` (no-op hasta decidir fuente de tasas).

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
