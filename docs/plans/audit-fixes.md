# Plan de arreglos — Auditoría pre-live (2026-05-28)

Origen: auditoría de bugs/contradicciones tras el cambio a Asian Session Scalper (D029).
Objetivo: dejar el bot apto para Phase 3 (live), sin bugs que rompan el ciclo de sesión,
y limpiar el código/documentación legacy de la estrategia anterior.

Decisiones asociadas: **D030–D036** (`docs/DECISIONS.md`).

Premisas fijadas con el usuario:
- Alcance: **todo, incluida la limpieza**.
- `data/drift.db` es **demo/vacía** — no hay datos reales que preservar.
- El time-stop de las 02:00 registra `close_reason = 'session_close'` (valor nuevo y explícito).

Convenciones del proyecto que aplican: UTC interno / UTC-5 presentación, config en YAML nunca
hardcodeada, sin código muerto, tests actualizados en el mismo commit que el refactor.
Tras cada step: `ruff check . && ruff format .` y `pytest`. Commit por step.

---

## Batch A — Crítico + Alto (bloquea live)

### Step 1 — DB: aceptar `session_close` y quitar `friday_close` (CRÍTICO)
**Problema:** `main.py` escribe `close_reason="session_close"` en cada cierre de sesión 02:00,
pero el `CHECK` de `trades.close_reason` (`drift/db.py:33-36`) no lo admite → `IntegrityError`
nocturno que pausa el bot y deja posiciones sin cerrar. `friday_close` sigue en el enum y en
las etiquetas de Telegram pero ya no hay ruta que lo escriba (estrategia anterior).

**Cambios:**
- `drift/db.py` `_SCHEMA_SQL`: `close_reason` CHECK pasa a
  `('trailing_stop', 'take_profit', 'stop_loss', 'manual', 'drawdown_pause', 'session_close')`.
  (Se elimina `friday_close`, se añade `session_close`.)
- Añadir migración del esquema de `trades` análoga a la de `signals`: SQLite no permite
  `ALTER` de un `CHECK`, así que se hace **rebuild** guardado (crear `trades` nueva con el
  CHECK correcto, `INSERT … SELECT` desde la vieja, `DROP`, `RENAME`). Idempotente: detectar
  si el CHECK viejo está presente (p.ej. leyendo `sqlite_master.sql` y buscando `'friday_close'`).
  Como la DB es demo, la migración debe funcionar tanto con tabla vacía como con datos.
- `drift/telegram_bot.py:37`: quitar la etiqueta `"friday_close"`; añadir
  `"session_close": "Session close (02:00)"`.

**Aceptación:**
- `close_trade_record(..., close_reason="session_close")` inserta sin error en una DB recién
  inicializada y en una DB con el esquema viejo (tras `init_db`).
- `init_db` sobre una DB con el CHECK viejo migra sin pérdida de filas y sin volver a migrar
  en el segundo arranque.
- Telegram muestra "Session close (02:00)" para esos cierres.

### Step 2 — Cierre de sesión robusto y bajo lock (ALTO)
**Problema:** `_close_session_trades` (ruta de estado D en `main.py`) corre **sin** `_trade_lock`
mientras el hilo de monitoreo sí lo toma (race en `order_send`/DB). Además, el `for pos in
mt5_positions` no aísla errores: una excepción en la primera posición deja las demás **abiertas
en el broker** (anula el time-stop) y pausa el bot entero.

**Cambios (`main.py`):**
- Ejecutar el cuerpo de `_close_session_trades` dentro de `with _trade_lock:` (coherente con
  `_analyse_pair_m15` y `_monitoring_tick`).
- Envolver el procesamiento de **cada** posición en `try/except` propio: loguear y continuar
  con la siguiente; nunca abortar el lote ni propagar al main loop.
- Tras cerrar todas, resetear `session_states` (como ya hace).

**Aceptación:**
- Test: con 3 posiciones simuladas donde el registro DB de la 1ª falla, las otras 2 se cierran
  igualmente y el bot no queda pausado.
- No hay ruta de cierre de sesión que adquiera/escriba sin `_trade_lock`.

### Step 3 — Etiqueta de cierre correcta con trailing desactivado (MEDIO→incluido en A)
**Problema:** `_detect_closed_trades` (`main.py:686`) etiqueta cierres de motivo desconocido
como `"trailing_stop"` cuando `pnl > 0`, pese a `use_trailing_stop: false`. Distorsiona reportes.

**Cambios:** en el fallback (motivo MT5 ≠ SL/TP), usar `"manual"` en lugar de `"trailing_stop"`.
Mantener `stop_loss`/`take_profit` cuando el deal sí trae `DEAL_REASON_SL/TP`.

**Aceptación:** un cierre con `DEAL_REASON_CLIENT`/desconocido se registra como `manual`,
nunca `trailing_stop`, mientras el trailing esté desactivado.

---

## Batch B — Medio

### Step 4 — Reporte semanal manejado por config
**Problema:** `_check_weekly_report` (`main.py:555`) hardcodea "lunes 01:00 UTC"; los campos
`reports.weekly_report_day`, `weekly_report_hour` y `reports.timezone` se parsean pero **no se
leen**. Viola la convención "config nunca hardcodeada".

**Cambios:**
- Derivar el instante de disparo en UTC a partir de `weekly_report_day` + `weekly_report_hour`
  interpretados en `reports.timezone` (UTC-5 por defecto). Domingo 20:00 UTC-5 ⇒ lunes 01:00 UTC
  (debe seguir coincidiendo con el comportamiento actual cuando la config trae los defaults).
- Helper puro y testeable: `(weekday_utc, hour_utc) = _weekly_trigger_utc(reports_config)`.

**Aceptación:**
- Con la config por defecto, dispara exactamente lunes 01:xx UTC (sin regresión).
- Cambiar `weekly_report_hour`/`weekly_report_day` mueve el disparo de forma consistente.
- Test unitario del helper para varios offsets/días.

### Step 5 — Paridad backtest ⇄ vivo
**Problema:** `AsianSessionStrategy` (`backtest/asian_engine.py:108-113`) tiene defaults de clase
`sl 1.5 / ADX 25 / RSI 30·70 / range 1-3`, distintos de los parámetros vivos optimizados
(`2.5 / 35 / 35·65 / 1-4`, ver D029). `run_asian_backtest` solo pasa `sl_atr_mult` y
`adx_max_threshold`, así que un run "plano" (`backtest/run_asian.py`) **no refleja producción**.

**Cambios:**
- Actualizar los defaults de clase de `AsianSessionStrategy` a los valores optimizados de D029.
- `run_asian_backtest` acepta y pasa los 6 parámetros (`sl_atr_mult, adx_max_threshold,
  rsi_oversold, rsi_overbought, range_atr_min, range_atr_max`).
- `backtest/run_asian.py` toma esos valores de `config.yaml` (fuente única de verdad) en lugar
  de defaults implícitos.

**Aceptación:** un run plano de `run_asian.py` usa exactamente los parámetros de `config.yaml`;
los defaults de clase coinciden con D029.

---

## Batch C — Limpieza (código/config/docs legacy)

### Step 6 — Eliminar config y código muerto
**Candidatos confirmados sin uso en runtime** (verificar con grep antes de borrar; actualizar
tests en el mismo commit):
- Config: `system.friday_close_hour_utc`, `risk.take_profit_ratio`,
  `risk.stop_loss_atr_multiplier`, `risk.take_profit_mode` — parseados pero nunca leídos.
  Quitar de `RiskConfig`/`SystemConfig`, de `_parse_*`, de `config.example.yaml` y `config.yaml`.
- `drift/risk.py:calculate_sl_tp` y `drift/strategy.py:check_tp_hit` — solo usados por tests.
  Eliminar funciones + sus tests (`tests/test_e2e.py`, `tests/test_strategy.py`).
- `drift/indicators.py`: `compute_ema`, `compute_macd`, `compute_rsi`, `compute_adx`,
  `compute_bollinger_bands` — sin consumidores en runtime (solo `compute_atr` se usa, vía
  `trailing.py`). Verificar tests/backtest; eliminar los que queden sin uso.
- `StrategyConfig` campos legacy: `timeframe_trend/entry`, `ema_fast/slow/entry`,
  `macd_fast/slow/signal`, `adx_threshold`, `ema_gap_threshold`, y los genéricos
  `adx_period/rsi_period/atr_period` si están duplicados por `m15_*`/`h4_adx_period`.
  Borrar solo los que grep confirme sin uso.

**Aceptación:** `ruff check .` limpio, `pytest` verde, ningún símbolo eliminado referenciado.

### Step 7 — `DEFAULT_PAIRS` coherente con la documentación
**Problema:** `config.py:88` `DEFAULT_PAIRS = [AUDCAD, NZDCAD, AUDNZD, EURCHF, EURGBP]` no coincide
con los 5 pares de D029/`config.yaml`/`CLAUDE.md`.

**Cambios:** `DEFAULT_PAIRS = ["AUDNZD", "EURCHF", "EURJPY", "GBPJPY", "EURGBP"]`.

**Aceptación:** el fallback por defecto coincide con `config.yaml`.

### Step 8 — Sincronizar documentación
**Problema:** `PROGRESS.md` describe la estrategia vieja (trend following EMA/MACD, loop H4) y
contradice `CLAUDE.md` (Asian Scalper). Posibles referencias residuales a `friday_close` /
trend-following en `docs/ARCHITECTURE.md` y `docs/user/`.

**Cambios:**
- Reescribir `PROGRESS.md` para reflejar la realidad: Phases 1-2 hechas sobre Asian Scalper,
  notas legacy archivadas; añadir esta tanda como "Phase 2.5 — Audit fixes".
- Barrer `docs/ARCHITECTURE.md` y `docs/user/*` por menciones a `friday_close`, trailing activo,
  EMA/MACD o ventana H4 y corregirlas.

**Aceptación:** ningún doc activo contradice a `CLAUDE.md` ni menciona `friday_close`.

### Step 9 — Tests de regresión del ciclo de sesión
Cubrir lo que la auditoría reveló sin cobertura:
- Cierre de sesión 02:00: la DB acepta `session_close`; con N posiciones, todas se cierran aunque
  una falle; el bot no se pausa por ello.
- `_detect_closed_trades`: motivo desconocido ⇒ `manual` (no `trailing_stop`).
- `_weekly_trigger_utc`: defaults ⇒ lunes 01:00 UTC; cambios de config mueven el disparo.
- Migración de `trades`: DB vieja → nueva sin pérdida, idempotente.

**Aceptación:** nuevos tests pasan; suite completa verde.

---

## Orden de ejecución y paralelismo
- **Secuencial dentro de A** (Step 1 → 2 → 3): tocan DB/`main.py` y se solapan.
- **B y C en paralelo tras A.** Independientes entre sí:
  - Step 4 (`main.py` reporte) — aislado.
  - Step 5 (`backtest/`) — aislado.
  - Steps 6/7 (`config.py`, `risk.py`, `indicators.py`) — aislados de B.
  - Step 8 (docs) — aislado.
- **Step 9 al final**, una vez estabilizado el comportamiento.

## Definition of done del lote
- `ruff check . && ruff format .` limpio; `pytest` verde.
- El bot completa un ciclo simulado 21:00→02:00 con cierre de sesión registrado y sin pausa.
- `docs/DECISIONS.md` D030–D036 reflejan estas decisiones; `PROGRESS.md` actualizado.
- Ninguna config parseada queda sin consumir; ningún doc activo contradice `CLAUDE.md`.
