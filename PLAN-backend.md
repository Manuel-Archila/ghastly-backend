# Ghastly — Plan del Backend

API de finanzas personales. Documento de arquitectura y ejecución.
Complemento de [`PLAN-finanzas-app.md`](./PLAN-finanzas-app.md) (plan de producto) y de `PLAN-frontend.md` en `ghastly-frontend`.

---

## 1. Decisión de arquitectura

El plan de producto era **local-first sin backend**. Al separar en dos repos, la arquitectura pasa a ser:

```
┌─────────────────────────┐        ┌──────────────────────────┐
│  ghastly-frontend       │        │  ghastly-backend         │
│  Expo / React Native    │        │  FastAPI + Postgres      │
│                         │        │                          │
│  SQLite (Drizzle)  ◀────┼── sync ┼──▶ fuente de verdad      │
│  = caché + outbox       │        │  = motor de cálculo      │
│  UI 100% offline        │        │  = reglas de negocio     │
└─────────────────────────┘        └──────────────────────────┘
```

**El principio local-first NO se abandona.** Se traduce así:

| Regla | Implicación para el backend |
|---|---|
| Todo funciona offline | Ningún endpoint es requisito para usar la app. El cliente escribe local y encola. |
| El servidor es la fuente de verdad | En conflicto, gana el servidor salvo en campos editados por el usuario (ver §7). |
| Los IDs los genera el cliente | **UUIDv7 generados en el dispositivo.** El servidor nunca asigna IDs de entidades de dominio. Esto hace la sincronización idempotente y permite crear offline sin reconciliar. |
| El backend es idempotente | Toda mutación acepta `Idempotency-Key`. Reintentar nunca duplica. |

**Por qué backend si es un solo usuario:** multi-dispositivo (teléfono + tablet + futura web), backup real, motores pesados (recurrencia, amortización, anomalías) fuera del hilo de UI, y notificaciones push de vencimientos aunque la app esté cerrada.

---

## 2. Stack

Sigue las convenciones de `clinix_backend` / `certifac-invoice-api`.

| Capa | Elección |
|---|---|
| Runtime | Python 3.12 |
| Framework | FastAPI (`fastapi[standard]`) + Uvicorn |
| DB | PostgreSQL 16 |
| ORM | SQLAlchemy 2.0 async + `asyncpg` |
| Migraciones | **pyway** — SQL versionado `V1_0__initial_schema.sql` |
| Validación | Pydantic v2 + `pydantic-settings` |
| Auth | JWT (`python-jose`) access + refresh, hash con `argon2-cffi` |
| Logs | `structlog` (JSON estructurado, `request_id` en cada línea) |
| Rate limit | `slowapi` |
| Archivos | `boto3` → S3 (recibos), URLs prefirmadas |
| Jobs | APScheduler in-process (Fase 3) → migrar a worker si crece |
| Lint / tipos | `ruff` (line-length 100) + `mypy` |
| Tests | `pytest` (`asyncio_mode = "auto"`), `httpx`, `testcontainers[postgres]` |

Deps sin discusión: nada de ORM alterno, nada de Celery/Redis hasta que un job realmente lo pida.

---

## 3. Estructura de carpetas

```
ghastly-backend/
├── main.py                  # app, middlewares, include_router
├── config.py                # Settings (pydantic-settings)
├── logger.py                # structlog
├── dependencies.py          # get_db, get_current_user, require_scope
├── core/
│   ├── errors.py            # AppError, NotFoundError, ConflictError, ...
│   ├── response.py          # ok()
│   ├── middleware.py        # request_id, error handlers
│   ├── security.py          # JWT, argon2
│   ├── rate_limit.py
│   └── idempotency.py       # decorador/dep de Idempotency-Key
├── domain/                  # ⚠️ FUNCIONES PURAS. Sin FastAPI, sin SQLAlchemy.
│   ├── money.py             # Money: centavos, aritmética, formateo GTQ/USD
│   ├── balances.py          # derivación de saldo desde transacciones
│   ├── budget.py            # consumo, rollover, proyección
│   ├── installments.py      # generación de calendario de cuotas
│   ├── recurrence.py        # próxima ocurrencia, expansión de reglas
│   ├── amortization.py      # separación capital/interés
│   ├── credit_cycle.py      # corte y fecha de pago de tarjetas
│   └── anomalies.py         # detección de desviaciones
├── storage/
│   ├── db.py                # engine, session factory
│   └── models/              # modelos SQLAlchemy
├── schemas/                 # Pydantic in/out por dominio
├── services/                # orquestación: DB + domain + transacciones
├── routers/                 # HTTP puro: valida, llama al service, responde
├── jobs/                    # tareas programadas
├── migrations/              # V1_0__*.sql, pyway
├── tests/
│   ├── domain/              # rápidos, sin DB — la mayoría de los tests
│   └── api/                 # testcontainers + httpx
├── pyproject.toml
├── Dockerfile
└── docker-compose.yml
```

**Regla de oro:** un router nunca toca la DB directamente ni hace matemática de dinero. Un módulo de `domain/` nunca importa nada de FastAPI, SQLAlchemy ni de la red.

---

## 4. Convenciones transversales

### Envelope de respuesta

Igual que `clinix_backend`:

```python
class ApiResponse(BaseModel, Generic[T]):
    is_success: bool
    message: str      # en español, apto para mostrar al usuario
    data: T | None
```

Errores → mismo envelope con `is_success: false`, más un `code` estable en `data`:

```json
{
  "is_success": false,
  "message": "La cuenta destino no existe.",
  "data": { "code": "ACCOUNT_NOT_FOUND", "field": "to_account_id" }
}
```

El cliente **nunca** parsea `message` para tomar decisiones; usa `code`.

### Dinero

- Todos los montos: `BIGINT`, en **centavos**, nombre `*_cents`. Nunca `NUMERIC`, nunca `float`, nunca en la API.
- Toda operación aritmética pasa por `domain/money.py`. Prohibido `a["amount_cents"] + b["amount_cents"]` fuera de ahí.
- Redondeo: half-up, y **el residuo de un reparto se asigna a la última fracción** (cuotas: la última cuota absorbe la diferencia).

### Fechas y zona horaria

- Todo se almacena en UTC (`TIMESTAMPTZ`). Los campos de calendario (`date`, `due_date`, `statement_day`) son `DATE` sin hora.
- **La zona de negocio es `America/Guatemala` (UTC-6, sin DST).** El corte de "mes" lo define el servidor con esa zona, no la del dispositivo. Un gasto a las 23:00 del 31 pertenece a ese mes.
- Todo timestamp que sale de la API va en ISO 8601 con offset.

### Convenciones HTTP

- Prefijo `/v1`. Un cambio incompatible abre `/v2`; no se rompe `/v1`.
- Recursos en plural y kebab-case: `/v1/installment-plans`.
- `PATCH` para actualización parcial. `PUT` solo en configuración singleton.
- `DELETE` = borrado lógico (`deleted_at`) en transacciones; archivado (`is_archived`) en cuentas y categorías. El borrado físico no existe en la API.
- Paginación **por cursor** (nunca offset): `?limit=50&cursor=<opaco>` → `data.items[]` + `data.next_cursor`.
- Filtros de rango: `?from=YYYY-MM-DD&to=YYYY-MM-DD` (inclusive ambos).
- `Idempotency-Key` obligatorio en todo `POST` que cree dinero.

### Multiusuario desde el día uno

Aunque hoy el usuario es uno, **toda tabla de dominio lleva `user_id`** y toda query filtra por él. Sin esto, agregar "presupuesto compartido con pareja" (backlog) obliga a reescribir el esquema. Es gratis hacerlo ahora.

---

## 5. Modelo de datos

Todas las tablas comparten esta base:

```sql
id            UUID PRIMARY KEY,            -- UUIDv7 generado por el cliente
user_id       UUID NOT NULL REFERENCES users(id),
created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
deleted_at    TIMESTAMPTZ,                 -- borrado lógico
server_seq    BIGINT NOT NULL              -- ver §7, cursor de sincronización
```

### Tablas de infraestructura

**`users`** — `id, email, password_hash, name, base_currency (default 'GTQ'), timezone (default 'America/Guatemala'), locale, created_at`

**`refresh_tokens`** — `id, user_id, token_hash, device_id, expires_at, revoked_at, last_used_at`

**`devices`** — `id, user_id, platform, push_token, app_version, last_sync_seq, last_seen_at`

**`idempotency_keys`** — `key, user_id, endpoint, request_hash, response_body, status_code, created_at`. TTL 24 h. PK `(user_id, key)`.

**`change_log`** — bitácora de sincronización. `server_seq BIGSERIAL, user_id, entity_type, entity_id, op (upsert|delete), payload JSONB, device_id, created_at`.

**`fx_rates`** — `date, from_currency, to_currency, rate NUMERIC(18,8), source (manual|api)`. PK compuesta.

### Tablas de dominio

Definidas en `PLAN-finanzas-app.md` §3. Aquí solo lo que el backend agrega o precisa:

**`accounts`**
`name, type, currency, institution, last_four, initial_balance_cents, current_balance_cents, is_archived, color, icon, sort_order`
+ tarjetas: `credit_limit_cents, statement_day SMALLINT, payment_due_day SMALLINT, interest_rate NUMERIC(6,4)`
+ `balance_recalculated_at TIMESTAMPTZ`

> `current_balance_cents` es **caché derivada**. La verdad son las transacciones. Todo servicio que escribe transacciones marca la cuenta como sucia y recalcula en la misma transacción de DB. `POST /accounts/{id}/recalculate` es la válvula de escape.

**`categories`**
`name, kind, parent_id, icon, color, is_archived, is_tax_deductible, sort_order`
Constraint: `parent_id` solo puede apuntar a una categoría sin padre (**dos niveles, nunca tres**). Se valida con un `CHECK` + trigger o en el service; que quede en el service es aceptable, pero documentado.

**`transactions`**
`account_id, category_id, kind, amount_cents, currency, fx_rate, base_amount_cents, date, description, merchant, notes, transfer_group_id, installment_id, recurring_rule_id, refund_of_id, receivable_id, is_reconciled, is_tax_relevant, receipt_key, tags TEXT[]`

- `amount_cents` **siempre positivo**. El signo lo da `kind` + el lado de la transferencia. Un solo lugar decide el signo: `domain/balances.py`.
- `base_amount_cents` = monto convertido a GTQ **con la tasa del día de la compra, congelada**. Nunca se recalcula (caso 4 del plan).
- `refund_of_id` liga un reembolso a su gasto original (caso 2).
- Índices: `(user_id, date DESC)`, `(user_id, account_id, date DESC)`, `(user_id, category_id, date)`, `(transfer_group_id)`, GIN sobre `tags` y sobre `to_tsvector(description || merchant)` para la búsqueda.

**`budgets` / `budget_items` / `budget_periods`**
La tercera es nueva: al cerrar un mes se congela el resultado (`budget_period_items`: `spent_cents`, `rollover_in_cents`, `rollover_out_cents`). Sin esto, editar una transacción retroactiva (caso 11) reescribe la historia de presupuestos ya cerrados.

**`installment_plans` / `installments`**
`installments`: `plan_id, number, due_date, amount_cents, principal_cents, interest_cents, paid_at, transaction_id, status`

**`recurring_rules`** — como en el plan + `last_generated_at`, `last_amount_cents` (para detectar alzas de precio) y `price_history JSONB[]`.

**`debts` / `debt_payments`**
`debt_payments`: `debt_id, date, total_cents, principal_cents, interest_cents, fees_cents, transaction_id`

**`goals` / `goal_contributions`**

**`receivables`** — gastos compartidos (caso 3). `transaction_id, counterparty, amount_cents, settled_at, settlement_transaction_id`

**`transaction_templates`** — plantillas de gasto frecuente. `name, account_id, category_id, kind, amount_cents, description, use_count, last_used_at`

**`notification_preferences`** — singleton por usuario. `budget_alert_thresholds INT[]` (default `{80,100}`), `due_reminder_days`, `quiet_hours`, canales.

---

## 6. Reglas de negocio que vive el backend

Estas son las que hacen que la app no sea una calculadora rota. Cada una tiene test obligatorio en `tests/domain/`.

| # | Regla | Implementación |
|---|---|---|
| 1 | **Transferencias no son gasto** | Dos filas `kind='transfer'` con el mismo `transfer_group_id`, creadas atómicamente por `POST /transactions/transfer`. **Todo reporte excluye `kind='transfer'`.** Un solo helper, `exclude_transfers()`, usado por todos los queries de reporte. |
| 2 | **Reembolso** | `POST /transactions/{id}/refund` crea un `kind='income'` con `refund_of_id`. En reportes de categoría **resta del gasto de la categoría original**, no suma a ingresos. La regla vive en `domain/`, no en SQL disperso. |
| 3 | **Gasto compartido** | El gasto se registra completo. `receivables` guarda lo que me deben. El dashboard muestra "Por cobrar: Q X" aparte del patrimonio. Al liquidar se crea un ingreso ligado. |
| 4 | **Compra en USD** | Se guarda `amount_cents` (USD), `fx_rate` y `base_amount_cents` (GTQ). **Congelado.** Cambiar la tasa de hoy jamás toca transacciones pasadas. |
| 5 | **Pago de tarjeta** | Es transferencia (cuenta → tarjeta). El servicio lo **detecta y bloquea** si se intenta como gasto contra una cuenta `credit_card`, devolviendo `code: "USE_TRANSFER_FOR_CARD_PAYMENT"` para que el cliente ofrezca el flujo correcto. |
| 6 | **Cuotas** | Crear el plan **no genera un gasto por el total.** Genera N `installments` futuras. Solo la cuota del mes impacta presupuesto. El pasivo total sale de `SUM(installments no pagadas)` y se expone en el dashboard. |
| 7 | **Ingreso irregular** | El presupuesto soporta `income_basis`: `fixed` \| `previous_month` \| `avg_3m`. Endpoint `/reports/expected-income` para alimentar la sugerencia. |
| 8 | **Retiro de efectivo** | Transferencia a la cuenta tipo `cash`. Se sugiere automáticamente en el cliente; el backend solo la valida. |
| 9 | **Ajuste de saldo** | `POST /accounts/{id}/adjust` con el saldo real → crea una transacción `category='Ajuste de saldo'`, `is_reconciled=true`, con la diferencia y una nota obligatoria. Nunca se edita `current_balance_cents` a mano. |
| 10 | **Doble registro** | `GET /transactions/duplicates` y una advertencia en la respuesta de `POST /transactions`: si existe otra con mismo monto+cuenta en ±5 min, la respuesta incluye `data.warning = {code: "POSSIBLE_DUPLICATE", transaction_id}`. **Se crea igual** — advertir, no bloquear. |
| 11 | **Fechas retroactivas** | Permitido siempre. Recalcula saldos e impacta el presupuesto del mes al que pertenece la fecha, **salvo que ese período ya esté cerrado** en `budget_periods`: entonces se registra y se marca `affects_closed_period=true` para que el cliente lo señale. |
| 12 | **Aguinaldo / Bono 14** | `recurring_rules` con `frequency='yearly'`. Los reportes de promedio mensual exponen dos series: `avg_with_extraordinary` y `avg_recurring`. La segunda excluye ingresos marcados `is_extraordinary`. |

---

## 7. Sincronización

El corazón del backend. Modelo: **log de cambios + cursor monotónico por usuario**.

### Pull

```
GET /v1/sync/pull?since=0&limit=500
→ { changes: [{ entity_type, entity_id, op, payload, server_seq }], next_seq, has_more }
```

`server_seq` es un `BIGSERIAL` global. El cliente guarda el último `next_seq` recibido. Reconectar = pedir desde ahí. Sin timestamps, sin relojes desincronizados.

### Push

```
POST /v1/sync/push
{ device_id, mutations: [{ client_mutation_id, entity_type, entity_id, op, payload, client_updated_at }] }
→ { applied: [...], conflicts: [{ entity_id, reason, server_payload }], next_seq }
```

- `client_mutation_id` es UUID y se guarda; reenviar el mismo lote es no-op.
- Se aplica **en orden**, dentro de una transacción de DB por lote (o por mutación si el lote es grande; documentar la elección).
- Un `entity_id` que ya existe con el mismo contenido → `applied`, no error. Crear offline y reintentar debe ser inofensivo.

### Resolución de conflictos

| Situación | Resolución |
|---|---|
| Campos editables por el usuario (descripción, categoría, notas, tags) | **Last-write-wins por `client_updated_at`**, con el servidor como desempate. |
| Campos derivados (`current_balance_cents`, consumo de presupuesto, saldos de cuotas) | **Siempre gana el servidor.** El cliente nunca los envía; los recalcula localmente solo para pintar rápido. |
| Delete vs. update | **Gana el delete.** El borrado es lógico y reversible; el usuario puede restaurar. |
| Mismo `entity_id`, contenido distinto, ambos nuevos | Imposible por diseño: los IDs son UUIDv7 del cliente. Si pasa, es un bug; se registra en el log con severidad alta. |

### Regla operativa

Un cliente que estuvo offline dos semanas hace un push grande y luego un pull. **Nunca al revés.** El backend debe tolerar un push de 2000 mutaciones (paginar en el cliente en lotes de 200).

---

## 8. Catálogo de endpoints

Todos bajo `/v1`, todos autenticados salvo los marcados 🔓.

### Auth y dispositivos

| Método | Ruta | Notas |
|---|---|---|
| 🔓 POST | `/auth/register` | Deshabilitable por `ALLOW_REGISTRATION=false` |
| 🔓 POST | `/auth/login` | → `access_token` (15 min) + `refresh_token` (60 días) |
| 🔓 POST | `/auth/refresh` | Rotación de refresh token; reuso detectado ⇒ revoca la familia |
| POST | `/auth/logout` | Revoca el refresh del dispositivo |
| GET | `/auth/me` | Perfil + moneda base + preferencias |
| PATCH | `/auth/me` | |
| POST | `/auth/change-password` | Revoca todos los refresh salvo el actual |
| POST | `/devices` | Registra push token |
| DELETE | `/devices/{id}` | |

### Cuentas

| Método | Ruta | Notas |
|---|---|---|
| GET | `/accounts` | `?include_archived=false`. Devuelve saldo y, en tarjetas, el ciclo actual |
| POST | `/accounts` | |
| GET/PATCH | `/accounts/{id}` | |
| DELETE | `/accounts/{id}` | Archiva. 409 si tiene saldo ≠ 0 sin `?force=true` |
| POST | `/accounts/{id}/recalculate` | Recalcula el saldo derivado |
| POST | `/accounts/{id}/adjust` | Ajuste de saldo (caso 9) |
| GET | `/accounts/{id}/statement` | `?cycle=current\|previous\|YYYY-MM` — consumo del corte, fecha de pago, mínimo |
| PATCH | `/accounts/reorder` | `{ ids: [...] }` |

### Categorías

| Método | Ruta | Notas |
|---|---|---|
| GET | `/categories` | Árbol de 2 niveles |
| POST / PATCH / DELETE | `/categories[/{id}]` | DELETE archiva |
| POST | `/categories/seed` | Semilla del plan (§3). Idempotente |
| POST | `/categories/{id}/merge` | `{ into_id }` — reasigna transacciones y archiva |
| PATCH | `/categories/reorder` | |

### Transacciones

| Método | Ruta | Notas |
|---|---|---|
| GET | `/transactions` | Filtros: `from,to,account_id,category_id,kind,q,min_cents,max_cents,tags,is_reconciled,include_deleted`. Cursor. Agrupable por día en el cliente |
| POST | `/transactions` | `Idempotency-Key`. Puede devolver `warning: POSSIBLE_DUPLICATE` |
| GET/PATCH | `/transactions/{id}` | |
| DELETE | `/transactions/{id}` | Lógico |
| POST | `/transactions/{id}/restore` | Deshacer |
| POST | `/transactions/transfer` | Crea el par atómicamente |
| POST | `/transactions/{id}/refund` | |
| POST | `/transactions/bulk-categorize` | `{ ids: [], category_id }` |
| GET | `/transactions/duplicates` | `?window_minutes=5` |
| POST | `/transactions/{id}/receipt` | → URL prefirmada de subida |
| GET | `/transactions/stats` | Totales del filtro actual, sin traer las filas |
| GET/POST/DELETE | `/transaction-templates[/{id}]` | Ordenadas por `use_count` |

### Presupuestos

| Método | Ruta | Notas |
|---|---|---|
| GET | `/budgets` | |
| POST | `/budgets` | |
| GET/PATCH/DELETE | `/budgets/{id}` | |
| GET | `/budgets/current` | `?month=YYYY-MM` — **el endpoint más usado**: por categoría el presupuestado, gastado, disponible, % y **proyección de fin de mes** |
| PATCH | `/budgets/{id}/items/{item_id}` | |
| POST | `/budgets/{id}/copy-from-previous` | |
| POST | `/budgets/{id}/close-period` | Congela el mes y calcula rollovers |
| GET | `/budgets/{id}/history` | Períodos cerrados |

### Cuotas

| Método | Ruta | Notas |
|---|---|---|
| GET/POST | `/installment-plans` | POST genera el calendario completo |
| GET/PATCH/DELETE | `/installment-plans/{id}` | DELETE borra las cuotas no pagadas |
| GET | `/installment-plans/{id}/schedule` | |
| POST | `/installments/{id}/pay` | Crea la transacción y liga |
| GET | `/installments/upcoming` | `?days=30` |
| GET | `/installments/liability` | Pasivo total pendiente por mes |

### Recurrentes y suscripciones

| Método | Ruta | Notas |
|---|---|---|
| GET/POST | `/recurring-rules` | |
| GET/PATCH/DELETE | `/recurring-rules/{id}` | |
| POST | `/recurring-rules/{id}/pause` · `/resume` · `/skip-next` | |
| POST | `/recurring-rules/{id}/confirm` | Genera la transacción cuando `auto_create=false` |
| GET | `/recurring-rules/upcoming` | `?days=30` |
| GET | `/subscriptions/summary` | Total mensual, anualizado, alzas de precio detectadas, candidatas a cancelar |

### Deudas y metas

| Método | Ruta |
|---|---|
| GET/POST · GET/PATCH/DELETE | `/debts[/{id}]` |
| GET | `/debts/{id}/amortization` |
| POST | `/debts/{id}/payments` — separa capital/interés |
| GET/POST · GET/PATCH/DELETE | `/goals[/{id}]` |
| POST | `/goals/{id}/contribute` |

### Por cobrar

| Método | Ruta |
|---|---|
| GET/POST | `/receivables` |
| POST | `/receivables/{id}/settle` |

### Reportes

| Método | Ruta | Devuelve |
|---|---|---|
| GET | `/reports/dashboard` | `?month=` — patrimonio neto, flujo del mes, top 5 categorías, presupuesto global, próximos vencimientos, pasivo en cuotas, por cobrar. **Una sola llamada para la pantalla Hoy** |
| GET | `/reports/net-worth` | `?months=12` — serie de patrimonio |
| GET | `/reports/cashflow` | `?from&to&granularity=month\|week` |
| GET | `/reports/by-category` | `?from&to&kind=expense` — para la dona |
| GET | `/reports/trends` | `?months=6` |
| GET | `/reports/comparison` | `?a=2026-08&b=2026-09` |
| GET | `/reports/anomalies` | `?month=` — "gastaste 40% más en X" |
| GET | `/reports/savings-rate` | `?months=12` |
| GET | `/reports/upcoming` | Calendario unificado: recurrentes + cuotas + tarjetas + deudas |
| GET | `/reports/expected-income` | Base para presupuesto con ingreso irregular |

### FX, sync, export, meta

| Método | Ruta | Notas |
|---|---|---|
| GET | `/fx/rate` | `?from=USD&to=GTQ&date=` |
| PUT | `/fx/rates` | Carga manual |
| GET | `/sync/pull` · POST `/sync/push` · GET `/sync/status` | §7 |
| POST | `/export` | `{format: csv\|xlsx\|json, from, to}` → job |
| GET | `/export/{job_id}` | → URL prefirmada |
| POST | `/backup` · POST `/restore` | Snapshot cifrado completo |
| 🔓 GET | `/health` · `/health/ready` | |
| GET | `/info` | Versión, esquema de migración aplicado |
| 🔓 GET | `/docs` | OpenAPI |

---

## 9. Jobs programados

Corren con `APScheduler` en el proceso, a la hora de Guatemala. Cada job es **idempotente y re-ejecutable**: se identifica por `(rule_id, occurrence_date)`, así que correr dos veces el mismo día no duplica nada.

| Job | Cuándo | Qué hace |
|---|---|---|
| `generate_recurring` | 00:05 diario | Genera transacciones de reglas con `auto_create=true`; crea notificación pendiente para las de `auto_create=false` |
| `send_due_reminders` | 08:00 diario | Push de vencimientos según `reminder_days_before` |
| `check_budget_alerts` | tras cada escritura + 20:00 diario | Umbrales 80% / 100% |
| `close_budget_periods` | día 1, 00:15 | Cierra el mes anterior y calcula rollovers |
| `detect_anomalies` | día 1, 06:00 | Compara contra el promedio de 3 meses |
| `card_cycle_notices` | diario | Aviso de corte y de fecha de pago |
| `purge_idempotency_keys` | horario | TTL 24 h |

---

## 10. Seguridad

- Argon2id para contraseñas. Access token 15 min, refresh 60 días **con rotación y detección de reuso** (reuso ⇒ se revoca toda la familia de tokens del dispositivo).
- Rate limit: `/auth/login` 5/min por IP, escritura 60/min por usuario, `/sync/push` 10/min con lotes grandes permitidos.
- **Toda query filtra por `user_id`.** Se aplica en el service, no se confía en el router. Test de autorización obligatorio: usuario A no puede leer ni escribir nada de B, en cada endpoint con `{id}`.
- Recibos en S3 privado, solo URLs prefirmadas con expiración de 5 min.
- Sin PII en logs: nunca montos ni descripciones; sí IDs y `request_id`.
- CORS restringido por entorno. La app móvil no lo necesita; la futura web sí.
- El backup exportado va cifrado con clave derivada de la contraseña del usuario. El servidor no guarda esa clave.

---

## 11. Testing

Pirámide, no uniforme:

| Nivel | Dónde | Qué |
|---|---|---|
| **Dominio (la mayoría)** | `tests/domain/` | `Money`, saldos, presupuesto + rollover, generación de cuotas (incluido el residuo de centavos), recurrencia (fin de mes: día 31 en febrero), amortización, ciclo de tarjeta, anomalías. Sin DB, milisegundos. |
| Servicios | `tests/services/` | Transferencia atómica, reembolso, ajuste de saldo, detección de duplicados, transaccionalidad. |
| API | `tests/api/` | Testcontainers Postgres + httpx. Contrato, autorización cruzada, idempotencia, paginación por cursor. |
| Sincronización | `tests/sync/` | **Escenario obligatorio:** dos dispositivos offline editan la misma transacción, ambos hacen push, el resultado converge y ningún saldo queda mal. |

Los 12 casos de la §6 tienen, cada uno, un test con su número en el nombre: `test_case_06_installments_do_not_hit_budget`.

---

## 12. Fases de entrega

Cada fase termina con la API desplegable y con la app funcionando contra ella.

| Fase | Alcance | Entregable |
|---|---|---|
| **0 — Fundaciones** | Estructura, `config`, `logger`, `core/*`, Docker + Postgres, `V1_0__initial_schema.sql` con **todas** las tablas hasta Fase 5, `domain/money.py` con tests primero, `/health`, `/auth/*` | La API arranca, migra y autentica |
| **1 — Núcleo transaccional** | Cuentas, categorías + semilla, transacciones (CRUD, transferencia, reembolso), derivación de saldos, `/sync/*` | El cliente puede registrar y sincronizar toda la vida financiera |
| **2 — Presupuestos** | Budgets, `/budgets/current` con proyección, rollover, cierre de período, alertas | Control del mes en curso |
| **3 — Recurrentes, cuotas, deudas** | Los tres motores + jobs + push + ciclos de tarjeta | Ningún pago sorprende |
| **4 — Reportes** | Todos los `/reports/*`, anomalías, tasa de ahorro | Patrones, no solo números |
| **5 — Robustez** | Export, backup cifrado, receivables, plantillas, recibos en S3 | Producción |

**Orden dentro de cada fase, sin excepción:** `domain` + sus tests → `storage` + migración → `services` → `schemas` → `routers` → tests de API. No se avanza de capa con tests rojos.

---

## 13. Reglas para trabajar en este repo

- La lógica de negocio vive en `domain/` como funciones puras. Si un cálculo necesita la DB para existir, está mal modelado.
- Nunca `float` para dinero. Nunca. Ni en un log.
- Migraciones versionadas con pyway. **Una migración aplicada jamás se edita**: se escribe la siguiente.
- Cada endpoint nuevo: schema Pydantic de entrada y salida, filtro por `user_id`, y test de autorización cruzada.
- Antes de instalar una dependencia, evaluar si son 40 líneas propias.
- Los mensajes de la API van en español; los `code` en inglés y estables.
- Commits pequeños y descriptivos.

---

## 14. Preguntas abiertas

- ¿Push notifications con Expo Push o APNs/FCM directo?
- ¿Fuente de tasas de cambio, o solo carga manual?
- ¿Se arranca con histórico o desde cero? (define si hay que priorizar la importación de CSV)

### Decididas (2026-09-09)

- **Dónde se despliega:** todavía no decidido, y no bloquea nada — el `Dockerfile` ya es genérico y todas las credenciales (DB, S3) viajan por variables de entorno, así que funciona igual en Fly.io, Railway, un VPS o lo que se elija después.
- **Almacenamiento de objetos (recibos en S3, archivos de `/export`):** **Cloudflare R2**. Es compatible con la API de S3 (mismo `boto3`, solo cambia `endpoint_url` a la URL de la cuenta de R2) y no cobra por egreso. `config.py` guarda `s3_endpoint_url` además de las credenciales — si algún día se migra a AWS S3 real, alcanza con vaciar esa variable.
- **Backup cifrado (`POST /backup` / `POST /restore`):** el cliente reenvía la contraseña de login en el body de la petición (viaja por HTTPS, igual que en `/auth/login`). El servidor la usa un instante para derivar una clave de cifrado con Argon2id + un salt propio del backup (mismo mecanismo que ya protege las contraseñas guardadas), cifra o descifra, y **descarta la clave sin persistirla en ningún lado** — ni la clave derivada ni la contraseña quedan en la base de datos. Trade-off aceptado: si el usuario cambia su contraseña, un backup viejo cifrado con la contraseña anterior deja de poder restaurarse con la nueva (tendría que recordar cuál usó).
