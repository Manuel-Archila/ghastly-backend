# Plan de proyecto — App de finanzas personales (móvil, Guatemala)

Documento para usar como base en Claude Code. Está pensado para pegarse en `PLAN.md` del repo y trabajarlo por fases.

---

## 1. Contexto y decisiones tomadas

- **Usuario:** una sola persona (yo), Guatemala.
- **Moneda base:** GTQ. Soporte secundario para USD (cuentas en dólares y compras internacionales).
- **Entrada de datos:** carga manual. La app debe optimizar para que registrar un gasto tome menos de 10 segundos.
- **Plataforma:** app móvil nativa.
- **Conexión bancaria:** no hay API. Las "cuentas del banco" son referencias manuales con saldo que yo concilio.

---

## 2. Stack propuesto

| Capa | Elección | Por qué |
|---|---|---|
| App | React Native + Expo (dev build) | Nativo real, un solo código, buen ecosistema |
| Lenguaje | TypeScript estricto | El dominio financiero necesita tipos |
| DB local | SQLite (`expo-sqlite`) + Drizzle ORM | Local-first, funciona sin internet, migraciones versionadas |
| Estado | Zustand o TanStack Query sobre la capa de datos | Simple, sin boilerplate |
| Navegación | Expo Router | File-based, menos configuración |
| Gráficas | Victory Native o react-native-gifted-charts | |
| Fechas | date-fns con timezone `America/Guatemala` | |
| Dinero | Enteros en centavos + `dinero.js`. **Nunca `float`.** | |
| Tests | Vitest/Jest para dominio, Maestro para E2E | |

**Alternativa si se quiere 100% nativo:** Kotlin + Jetpack Compose + Room (solo Android). Recomiendo Expo salvo que haya una razón fuerte para lo contrario.

**Principio rector:** local-first. Todo funciona offline. La sincronización a la nube es opcional y viene al final.

---

## 3. Modelo de datos

Tablas principales. Todos los montos en centavos (`INTEGER`), todas las fechas en ISO 8601.

### `accounts` — cuentas
```
id, name, type, currency, institution, last_four,
initial_balance_cents, current_balance_cents,
is_archived, color, icon, sort_order, created_at
```
- `type`: `checking` | `savings` | `credit_card` | `cash` | `investment` | `loan` | `digital_wallet`
- Para `credit_card` se extiende con: `credit_limit_cents`, `statement_day` (fecha de corte), `payment_due_day` (fecha de pago), `interest_rate`.
- El saldo se **deriva** de las transacciones, pero se cachea en `current_balance_cents` por rendimiento. Debe haber una función `recalculateBalance(accountId)`.

### `categories` — categorías
```
id, name, kind, parent_id, icon, color, is_archived,
is_tax_deductible, sort_order
```
- `kind`: `expense` | `income`
- Jerarquía de dos niveles (categoría → subcategoría). No más profundo.
- Semilla inicial sugerida: Vivienda, Alimentación, Transporte, Salud, Educación, Servicios (luz/agua/internet/teléfono), Entretenimiento, Ropa, Mascotas, Deudas e intereses, Impuestos, Ahorro e inversión, Regalos y donaciones, Otros. Ingresos: Salario, Honorarios/Freelance, Ventas, Intereses, Reembolsos, Otros.

### `transactions` — movimientos
```
id, account_id, category_id, kind, amount_cents, currency,
fx_rate, date, description, merchant, notes,
transfer_group_id, installment_plan_id, recurring_rule_id,
is_reconciled, is_tax_relevant, receipt_uri, tags,
created_at, updated_at, deleted_at
```
- `kind`: `expense` | `income` | `transfer`
- **Transferencias:** se guardan como dos filas (salida y entrada) unidas por `transfer_group_id`. Nunca cuentan como gasto ni ingreso en los reportes.
- `deleted_at` para borrado lógico (permite deshacer).

### `budgets` y `budget_items` — presupuestos
```
budgets:      id, name, period_type, start_date, is_active, rollover_enabled
budget_items: id, budget_id, category_id, amount_cents, rollover_balance_cents
```
- `period_type`: `monthly` | `weekly` | `custom`
- **Rollover:** si sobra en una categoría, opcionalmente se acumula al mes siguiente. Configurable por ítem.
- Presupuesto por categoría **y** un límite global opcional.

### `installment_plans` — compras a cuotas
Caso muy común en Guatemala (meses sin intereses / financiamiento de tarjeta).
```
id, account_id, category_id, description, merchant,
total_amount_cents, installments_count, installment_amount_cents,
interest_rate, first_payment_date, status, created_at
```
- Al crear el plan se generan las `installments` futuras (tabla hija con `due_date`, `amount_cents`, `paid_at`, `transaction_id`).
- **Regla clave:** la compra completa NO se registra como gasto del mes. Solo la cuota del mes corriente impacta el presupuesto. Pero el **pasivo total pendiente** sí debe verse en el dashboard.

### `recurring_rules` — suscripciones y pagos recurrentes
```
id, account_id, category_id, kind, name, amount_cents, currency,
frequency, interval, day_of_month, next_due_date, end_date,
auto_create, reminder_days_before, status, last_generated_at
```
- `frequency`: `daily` | `weekly` | `monthly` | `quarterly` | `yearly`
- `auto_create`: si es `true` genera la transacción sola en la fecha; si es `false`, solo notifica y espera confirmación.
- Cubre: Netflix/Spotify, alquiler, colegiatura, gimnasio, seguros (anuales), impuestos periódicos, préstamos.
- Pantalla dedicada de **Suscripciones** con el costo mensual total y el anualizado. Alertar suscripciones sin usar o que subieron de precio.

### `debts` — préstamos y deudas
```
id, name, type, principal_cents, balance_cents, interest_rate,
monthly_payment_cents, start_date, term_months, linked_account_id
```
- Separar en cada pago el capital del interés. El interés es gasto; el capital es reducción de pasivo.

### `goals` — metas de ahorro
```
id, name, target_amount_cents, current_amount_cents,
target_date, linked_account_id, icon
```

---

## 4. Funcionalidades por fase

### Fase 0 — Fundaciones
- Repo, TypeScript estricto, ESLint + Prettier, estructura de carpetas por dominio (`/domain`, `/data`, `/ui`, `/features`).
- SQLite + Drizzle con sistema de migraciones.
- Utilidad `Money` (centavos, suma, resta, formateo GTQ/USD). Tests primero.
- Tema, tipografía, componentes base (botón, input, lista, sheet).
- **Entregable:** app que abre, con DB creada y una pantalla vacía.

### Fase 1 — Núcleo transaccional
- CRUD de cuentas y categorías (con semillas).
- CRUD de transacciones: gasto, ingreso, transferencia.
- **Pantalla de captura rápida:** teclado numérico grande, categoría, cuenta, fecha (default hoy), descripción opcional. Meta: menos de 10 segundos.
- Lista de movimientos con búsqueda, filtros y agrupación por día.
- Cálculo y visualización de saldos por cuenta.
- **Entregable:** puedo registrar toda mi vida financiera a mano.

### Fase 2 — Presupuestos
- Crear presupuesto mensual por categoría.
- Vista de progreso: gastado vs. presupuestado, barras con colores y proyección ("a este ritmo terminás en Q X").
- Rollover de saldos.
- Alertas al 80% y al 100% de una categoría.
- Copiar el presupuesto del mes anterior.
- **Entregable:** control real del mes en curso.

### Fase 3 — Recurrentes, cuotas y deudas
- Motor de recurrencia con generación automática o confirmación manual.
- Pantalla de suscripciones con total mensual/anual.
- Planes de cuotas con calendario de pagos y saldo pendiente.
- Deudas con amortización capital/interés.
- Notificaciones locales de vencimientos.
- Ciclos de tarjeta de crédito: cuánto llevo consumido en el corte actual y cuánto debo pagar.
- **Entregable:** ningún pago me agarra por sorpresa.

### Fase 4 — Reportes e inteligencia
- Dashboard: patrimonio neto, flujo de caja del mes, top categorías, tendencia 6/12 meses.
- Gasto por categoría (dona), evolución mensual (barras), ingreso vs. gasto.
- Comparativo mes contra mes y año contra año.
- Detección de anomalías: "gastaste 40% más en X este mes".
- Tasa de ahorro.
- **Entregable:** entiendo mis patrones, no solo mis números.

### Fase 5 — Robustez y calidad de vida
- Backup/restore a archivo cifrado y a iCloud/Google Drive.
- Exportar a CSV/Excel.
- Bloqueo por biométrico.
- Modo oscuro.
- Widget de pantalla de inicio con gasto del mes.
- Atajo de Siri / acción rápida para registrar gasto.
- Adjuntar foto de recibo.
- Plantillas de gastos frecuentes.
- Deshacer borrado.

### Backlog (no comprometido)
- Sincronización multi-dispositivo (requiere backend).
- Importación de CSV de banco cuando quiera migrar histórico.
- OCR de recibos.
- Presupuesto compartido con pareja.
- Manejo de inversiones y rendimientos.

---

## 5. Casos que no se deben olvidar

Estos son los que rompen las apps de finanzas mal diseñadas:

1. **Transferencias entre cuentas propias** no son gasto. Error clásico que infla los reportes.
2. **Reembolsos y devoluciones:** un gasto negativo debe restar de la categoría original, no sumar como ingreso.
3. **Gastos compartidos:** pago la cuenta de todos y me devuelven después. Necesito marcar "me deben Q X".
4. **Compras en dólares:** guardar el monto original, la tasa de cambio aplicada y el monto en GTQ. No recalcular después.
5. **Pago de tarjeta de crédito:** es una transferencia, no un gasto. El gasto ya se registró en cada compra.
6. **Cuotas:** el mes que compro un celular en 12 cuotas no gasté el celular completo. Ver sección `installment_plans`.
7. **Ingresos irregulares:** siendo Guatemala y probablemente con honorarios, los ingresos no son fijos. El presupuesto no debe asumir sueldo constante. Considerar presupuesto basado en "ingreso del mes anterior".
8. **Retiros de efectivo:** el retiro no es gasto, es transferencia a la cuenta "Efectivo". El gasto ocurre cuando uso el efectivo.
9. **Ajuste de saldo:** cuando el saldo real no cuadra con el de la app, permitir una transacción de ajuste que documente la diferencia.
10. **Doble registro:** advertir si registro dos transacciones idénticas en pocos minutos.
11. **Fechas retroactivas:** debo poder registrar algo de la semana pasada sin que se rompan los saldos ni los presupuestos ya cerrados.
12. **Aguinaldo y Bono 14:** ingresos anuales fijos en Guatemala. Deben poder proyectarse y no distorsionar el promedio mensual.

---

## 6. Reglas para el desarrollo (para `CLAUDE.md`)

- **Lógica de dominio primero.** Toda regla de negocio (cálculo de saldo, presupuesto, cuotas, impuestos) vive en `/domain` como funciones puras sin dependencias de React ni de la DB. Se testea sola.
- **Nunca `float` para dinero.** Solo enteros en centavos.
- **Migraciones siempre versionadas.** Nunca modificar una migración ya aplicada.
- **Cada fase termina con la app funcionando.** Nada de ramas largas a medio hacer.
- **Tests obligatorios** en: utilidad Money, cálculo de saldos, motor de presupuestos, generación de cuotas, motor de recurrencia.
- **Sin dependencias innecesarias.** Antes de instalar algo, evaluar si son 40 líneas propias.
- **Todo funciona offline.** Ninguna funcionalidad core puede depender de red.
- Commits pequeños y descriptivos.

---

## 7. Cómo arrancar en Claude Code

Sugerencia de primer prompt:

> Lee `PLAN.md`. Vamos a ejecutar la Fase 0. Antes de escribir código, propone la estructura de carpetas y el esquema completo de Drizzle para todas las tablas del plan, incluyendo las que se usan hasta la Fase 5, para no reescribir migraciones después. Espera mi aprobación antes de generar archivos.

Después, una fase por sesión:

> Fase 1. Empezá por la capa de dominio y sus tests, después la capa de datos, y al final la UI. No avances a la siguiente capa hasta que los tests pasen.

---

## 8. Preguntas abiertas

- ¿Cuántas cuentas y tarjetas voy a manejar realmente?
- ¿Quiero histórico de meses anteriores o arranco desde cero?
- ¿Android, iOS, o ambos?
