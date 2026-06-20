# Drift — Progress Tracker

## Phase 1 — MVP (Demo)

### Session 1: Foundation (Steps 1-4)
- [x] 1. Scaffolding del proyecto — estructura, venv, dependencias
- [x] 2. Módulo de configuración — config.yaml loader y validación
- [x] 3. Módulo de conexión MT5 — conectar, desconectar, health check, reconexión
- [x] 4. Módulo de datos — obtener velas D1 y H4 de MT5

### Session 2: Strategy + Validation (Steps 5-7)
- [x] 5. Módulo de indicadores — RSI, ATR, ADX (base para la estrategia activa)
- [x] 6. Backtest de validación — backtest rápido para confirmar mérito de la estrategia antes de demo
- [x] 7. Módulo de estrategia — lógica inicial de estrategia (iteraciones previas al Daily Lull Scalper)

### Session 3: Risk + Execution (Steps 8-10)
- [x] 8. Módulo de riesgo — position sizing, límites (4 trades, 2 por moneda), drawdown, comisiones
- [x] 9. Módulo de ejecución — abrir/cerrar trades, SL/TP
- [x] 10. Módulo de trailing stop — implementado; desactivado en Daily Lull Scalper (use_trailing_stop: false)

### Session 4: Database + Telegram (Steps 11-13)
- [x] 11. Base de datos SQLite — schema, CRUD trades/señales/eventos
- [x] 12. Módulo de Telegram — bot, comandos, notificaciones
- [x] 13. Loop principal — orquestador M15, ciclo completo (ventana 21:00-02:00 hora servidor MT5 (GMT+2/+3))

### Session 5: Safety + Testing (Steps 14-16)
- [x] 14. Sistemas de seguridad — drawdown, max trades, correlación, reconexión, error handling
- [x] 15. Reporte semanal — generación automática domingos 8pm UTC-5
- [x] 16. Testing en demo — flujo completo: señal → trade → notificación → logging

## Phase 2 — Backtesting completo

### Session 6: Backtest Setup (Steps 17-19)
- [x] 17. Integración completa con Backtesting.py
- [x] 18. Descarga de datos históricos (mín 2 años)
- [x] 19. Backtest masivo de los 6 pares

### Session 7: Analysis (Steps 20-21)
- [x] 20. Comparación y ranking de pares
- [x] 21. Optimización de parámetros (sin overfitting)

## Phase 2.5 — Audit fixes (pre-live)

Plan completo: `docs/plans/audit-fixes.md`. Decisiones: D030–D036.

### Batch A — Crítico + Alto
- [x] 1. DB: añadir `session_close` y quitar `friday_close` del CHECK + migración rebuild de `trades` (remapea filas `friday_close`→`session_close`)
- [x] 2. Cierre de sesión 02:00 bajo `_trade_lock` y aislado por posición (try/except)
- [x] 3. `_detect_closed_trades`: motivo desconocido ⇒ `manual`, no `trailing_stop`

### Batch B — Medio
- [x] 4. Reporte semanal derivado de `reports.*` config (no hardcode lunes 01:00 UTC)
- [x] 5. Paridad backtest⇄vivo: defaults de clase = D029 + run_lull pasa los 6 params desde config

### Batch C — Limpieza
- [x] 6. Eliminar config/código muerto (friday_close_hour_utc, take_profit_*, calculate_sl_tp, check_tp_hit, compute_* sin uso, campos legacy de StrategyConfig)
- [x] 7. `DEFAULT_PAIRS` = los 5 pares de D029
- [x] 8. Sincronizar docs (reescribir PROGRESS legacy, barrer ARCHITECTURE/docs por friday_close/EMA/MACD)
- [x] 9. Tests de regresión del ciclo de sesión (session_close, cierre aislado, weekly trigger, migración) — 21 tests nuevos, 102 en total

## Phase 2.6 — Plataforma multi-estrategia (pre-live)

Diseño: D050–D057, `ROADMAP.md` Phase 2.6, `docs/knowledge/strategy-framework.md`. Se ejecuta **antes** de la Phase 3 (framework primero, D050). Restricciones: HC1 (solo framework, Lull única estrategia), HC2 (una cuenta, presupuesto por estrategia + brake global), HC3 (timing heterogéneo), HC4 (Lull en paper).

> **En cada sesión: leer PROGRESS.md primero.** Los pasos abajo tienen dependencias — respétalas. Delegar a subagentes los módulos independientes (ver Parallelism).

Estado: implementada vía /orchestrate en rama `feat/multi-strategy-platform` (2026-06-08/09). Steps 27-35 hechos y verificados; suite 282 passed / 2 skipped (slow opt-in), ruff limpio. Step 36 (validación e2e en paper) pendiente — manual. Nota: Step 32 se dividió en 32a (next_wake/timing) + 32b (motor); se añadió un chunk de cleanup (C10) para la fase "contract" del expand/contract. Decisiones nuevas: D058 (scheduling por next_wake), D059 (backtest engine + warm-up ADX).

### Batch MS-A — Fundación (Steps 27-28)
- [x] 27. Contrato `Strategy` + registry + tipos (`drift/strategies/base.py`). (D051) — `4840e1c`
- [x] 28. Refactor de config a `strategies[]` con validación (magic único, allocations ≤100%) + migración de config.example.yaml. (D052/D053/D054) — `eb3867f`

### Batch MS-B — Módulos independientes (Steps 29-31) — corridos en paralelo (worktrees)
- [x] 29. Port del Daily Lull al contrato (`drift/strategies/daily_lull.py`), tests de equivalencia. (D051/D055) — `ad7ff3c`
- [x] 30. Migración de DB: `strategy` en trades/signals, `strategy_state`, ALTER idempotente. (D056) — `179e12f`
- [x] 31. Motor de riesgo de dos niveles (`drift/risk.py`): sizing notional + límites global/estrategia. (D053) — `e87d246`

### Batch MS-C — Motor (Step 32, dividido) — depende de 27-31
- [x] 32a. `next_wake` + port del timing (ventana/skip-Friday) a la estrategia, equivalencia vs main.py. (D058) — `aa6485f`
- [x] 32b. Motor genérico (`drift/engine.py` + reescritura del loop de `main.py`): scheduler por next_wake, MarketData dedup, despacho, gating de riesgo, atribución por magic. (D051) — `7082ab0` (+ fix `_shutdown` magic `be8f297`)

### Batch MS-D — Control y observabilidad (Steps 33-34)
- [x] 33. Monitoreo multi-estrategia: drawdown por estrategia → `strategy_state`, pausa coordinada motor↔monitor, cierres atribuidos por magic. (D053/D056) — `f46ef23`
- [x] 34. Telegram multi-estrategia: desglose por estrategia, `/pause [x]`, `/resume [x]`, `/strategies`, reporte semanal por estrategia. (D057) — `1631ecc`

### Batch MS-E — Backtest y validación (Steps 35-36)
- [x] 35. Backtest unificado (`backtest/engine.py`): loop propio que consume el mismo `on_bar`, equivalencia **bit-exact** vs lull_engine; repuntados run_lull/optimize. Alcance núcleo: lull_engine.py se conserva (analyze_*/validate_oos siguen usándolo). (D055/D059) — `6e36730`
- [x] (cleanup C10) Quitado shim transitorio + `drift/strategy.py`; golden tests; `Signal` repuntado a base; `closed_bars` a engine. — `8973aca`
- [x] 36. **Validación end-to-end en paper** (2026-06-10): config.yaml ya en esquema `strategies[]`; framework nuevo (Phase 2.6+2.7) desplegado en demo; paridad backtest **bit-exact** (4/4 slow tests: sintético + EURCHF/GBPJPY) + **sesión en vivo validada**: wake del sleep → def-rango → lock 5/5 (filtro ATR) → filtro ADX (bloqueó EURGBP 52.9 / AUDNZD 35.3) → 2 fills (EURJPY/GBPJPY) + 5 rechazos D046 (spread guard) → on_fill/traded → **D064 close_all en el time-stop 02:00** → reset + sleep. D062 seeding en vivo OK (baseline 1989.93, dd 1.33%, sin doble conteo). P&L sesión −25.24 (ambos por time-stop, ruido de 1 sesión). **Step 36 completo → Phase 3 desbloqueada** (diferida: acumular trades 1-2 meses antes de go-live).

#### Pendientes / follow-ups de la Phase 2.6
- ~~**Migrar `config.yaml` real** al esquema `strategies[]`~~ ✅ hecho (confirmado en la sesión en vivo 2026-06-10).
- **Fidelidad ADX live vs backtest (D059):** la `on_bar` viva pide 50 barras H4 → ADX no converge (≈39 vs ≈27 de serie completa) y rechaza ~60 entradas de la hora rollover que el backtest acepta. Decisión "cabeza fría" pendiente: alinear el warm-up H4 live con el backtest (subir el `count`) tras analizar el impacto.
- **Migración del tooling de research** (analyze_entry_hours/analyze_exclude_rollover/analyze_spread_cost/validate_oos) al nuevo `backtest/engine.py` y eliminación de `lull_engine.py` — diferido (re-validar D045/D046/OOS).
- **`optimize_lull.py`** ahora es más lento (~55s/par/combo) por correr `on_bar` sobre todo el histórico — inherente a D055.
- **Portfolio backtest** (varias estrategias en una curva de equity) — diferido hasta la estrategia #2 (D055).

## Phase 2.7 — Hardening del modelo de riesgo (pre-live)

Diseño: D062–D064, `ROADMAP.md` Phase 2.7. Origen: auditoría 2026-06-09 (ver nota abajo). Se ejecuta **antes** de la Phase 3 (el modelo de riesgo por estrategia debe ser correcto antes de capital real). Restricción: el Lull sigue en paper; el sembrado del baseline (Step 37) reinicia la curva de drawdown por estrategia desde el deploy (intencional).

Orden: 37 primero (helper canónico, todos dependen); 38/39 tras 37; 40/41/42 independientes (paralelizables, distintos archivos).

### Batch H-A — Modelo de equity (Step 37) — fundación
- [x] 37. **Equity canónica + baseline persistido** (D062, arregla #1, #5): columna `baseline_capital` en `strategy_state` (migración idempotente); función canónica equity/drawdown en `risk.py` que reemplaza las 3 implementaciones (engine/monitor/telegram); sembrado `baseline = balance_seed×alloc/100 − realized_lifetime_seed` + reset del peak inflado en la 1ª computación; fix `get_stats(strategy=)` peak. Tests.
  - Pure `seed_baseline_capital` en `risk.py`; orquestación canónica `evaluate_strategy_drawdown(conn, strategy, allocation_pct, balance, floating, max_dd) -> (ok, reason, equity, peak)` + `seed_strategy_baseline` (escritura directa) en `db.py`; engine y monitor reapuntados al helper único (telegram queda para Step 39). 299 tests verdes.

### Batch H-B — Resume + display (Steps 38-39) — dependen de 37
- [x] 38. **`/resume` resetea el peak por estrategia** (D063, arregla #2, #3): `reset_strategy_peak` (escritura directa); `/resume <estrategia>` resetea peak + limpia pausa; global sin tocar (diferido). Tests. (309 passed)
- [x] 39. **Telegram usa la equity canónica** (#4): nuevo `_strategy_equity_info` read-only (equity = baseline + realized + floating, sin mutar peak); `/balance` y `/strategies` muestran el drawdown canónico + equity viva; `cmd_resume` reusa el helper. Tests. (324 passed)

### Batch H-C — Pausa y limpieza (Steps 40-42) — independientes
- [x] 40. **Pausa bloquea solo aperturas** (D064, arregla #6, #9): `_active_strategies` agenda todas (incl. pausadas); `_dispatch` ya no salta pausadas; `on_bar` siempre corre; `open` bajo pausa va al path de rechazo (`_reject` + `on_order_rejected("paused")`), `close`/`close_all` siempre ejecutan (time-stop 02:00 cierra en kill switch); log de pausa 1×/estrategia/tick. Tests. (329 passed)
- [x] 41. **Limpieza** (#7, #10, #11): `drift/pricing.py` nuevo con pip helpers; `closed_bars` eliminado de engine (tests → EngineMarketData); `check_all_risk`/`StrategyConfig` eliminados (tests migrados a DailyLullParams + helpers directos); notif SESSION CLOSED silenciada si 0 posiciones. (327 passed)
- [x] 42. **Reintentos fuera del lock** (#8): `_handle_open` en 3 fases — `_gate_and_size` bajo `_trade_lock` → `open_trade`+retries SIN lock → re-lock para `_record_open`/`on_fill`; test propio prueba que el lock está libre durante la llamada al broker.

#### Follow-ups de la Phase 2.7
- **Reset del peak global en `/resume` global** — diferido (D063): el peak global se almacena event-based (`peak_balance` + `MAX` en `get_stats`); bajarlo limpio necesita su propio diseño (tabla con columna directa o evento `peak_reset`). El freno global conserva su rigidez hasta entonces.
- **Re-seed del baseline al cambiar `allocation_pct`** (D062): cambiar la asignación deja el baseline obsoleto; poner `baseline_capital` a NULL fuerza el recálculo. Sin automatizar.

## Phase 2.8 — Estrategia #2: London Opening-Range Breakout (`london_orb`)

Diseño: **D067–D069**, `ROADMAP.md` Phase 2.8, `docs/knowledge/london-orb.md`, `docs/knowledge/strategy-framework.md` §7/§9. Primera estrategia nueva sobre el framework (la #2). Pivota el capital fuera del Daily Lull (pausado/desactivado, D069). Origen: post-mortem del Lull en vivo (auditoría 2026-06-19, memoria `daily-lull-live-lessons`).

> **En cada sesión: leer PROGRESS.md primero.** El framework ya existe (Phase 2.6/2.7) — esto es añadir una estrategia siguiendo el checklist de `strategy-framework.md` §7. Restricciones no negociables (§9): R:R ≥ 1:1 que sobreviva al spread real · backtest con costos de ejecución · time-stop que respete la duración del trade ganador · heartbeat en todo camino silencioso · riesgo aislado (magic_offset 1, allocation ≤100%).

> **Estado tras la orquestación (2026-06-19/20, `/orchestrate` en rama `feat/london-orb`):** estrategia implementada y backtesteada. **HALLAZGO: sin edge en backtest** (portfolio PF 0.82, ningún par OOS PF ≥ 1.2; la señal cruda es ≈ moneda al aire una vez la geometría es honesta). Re-anclada la geometría al precio de ruptura (D070). Desplegada en **paper como validación de sistema + fidelidad live-vs-backtest, NO de edge** (decisión del usuario; ver D070). Tras ~1 semana: archivar vs filtro de tendencia H1/H4. Status detallado: `docs/plans/london-orb.status.md`.

### Batch ORB-A — Estrategia (Steps 43-45)
- [x] 43. `LondonOrbParams` + parsing en `drift/strategies/london_orb.py`. (D067) — `aba10ff`
- [x] 44. `LondonOrbStrategy.on_bar`: estado per-par, define-range 10:00–11:00, lock + filtro ancho, ruptura por cierre M15 ambos sentidos, `CloseAll` time-stop 18:00, 1 trade/par/día; registrado; sin tocar MT5/DB/reloj. (D067/D068) — `aba10ff`. **Fix de paridad:** ventana keyed en `bar_time.hour` (no boundary) — `dc01bf0`. **Geometría re-anclada** (D070) — `ad9424e`.
- [x] 44b. Heartbeat de cierre 18:00: el `_handle_close_all` del engine ya es genérico (dispara con `reason=="session_close"` para cualquier estrategia, incl. 0 posiciones). Sin cambios al engine.
- [x] 45. Unit tests `tests/test_london_orb_strategy.py` (25 tests: long/short, filtros, ventana, time-stop, heartbeat, regresión de boundary parity, invariante R:R). 

### Batch ORB-B — Validación con costos reales (Steps 46-47)
- [x] 46. Datos M15 2a reales de los 4 pares (GBPJPY/EURJPY del Lull; GBPUSD/EURUSD descargados de MT5 demo). Motor generalizado para TP de broker intrabar (`e84eec2`, conserva el midpoint-close del Lull + 11 slow equivalence tests verdes). Runner `backtest/run_orb.py` + walk-forward (`472f8c2`). **Resultado: sin edge** (ver D070). `range_atr_min` inerte (el pip floor ata). 
- [~] 47. **Superseded por el demo** (D070): el paper en vivo es el test de spread/costo real, en vez de modelarlo en backtest. (El backtest honesto ya da PF<1 sin spread.)

### Batch ORB-C — Config, docs, deploy (Steps 48-50)
- [x] 48. Config: `config.yaml` real → `daily_lull` `enabled:false`, `london_orb` activo (magic 234001, alloc 100, 4 pares, params neutros). Validado (magic único, allocations ≤100%, instancia OK). `config.example.yaml` ya tenía el bloque (spec). (D069/D070)
- [~] 49. Docs: `configuration.md` (bloque london_orb — de la sesión spec); `london-orb.md` actualizado con geometría re-anclada + estado paper (D070) + horarios Bogotá corregidos (02:00–10:00, no 05:00–13:00). `getting-started.md` (ventana de la #2) pendiente menor.
- [ ] 50. **Validación e2e en paper — DOS estrategias en paralelo, ~2 semanas (EN CURSO, D071):** `daily_lull` + `london_orb` activas, independientes (global max_open 8 = 4+4, alloc 50/50). Primera corrida real multi-estrategia → valida el aislamiento de riesgo (D052/D053) en vivo. Objetivo: sistema + **fidelidad live-vs-backtest**, NO P&L. Lull se observa con D066 (su fallo R:R<spread es estructural, no se arregla observando). **Revisión ~2026-07-06:** por estrategia, archivar vs rediseñar (Lull R:R) / filtro tendencia (ORB).

## Phase 3 — Live

### Session 8: Go Live (Steps 22-26)
- [ ] 22. Migrar a VPS Windows
- [ ] 23. Configurar cuenta live ICMarkets
- [ ] 24. Ajustar config.yaml con credenciales live
- [ ] 25. Monitoreo intensivo primera semana
- [ ] 26. Operación autónoma con journal dominical

---

## Notes

> **Pivote de estrategia (D029):** La estrategia activa es **Daily Lull Scalper M15**
> (ver `CLAUDE.md`). Las iteraciones anteriores (trend-following EMA/MACD, mean-reversion H4)
> fueron el camino hasta llegar aquí — ver D029 para el historial completo. La infraestructura
> (risk/executor/db/telegram/main loop) se construyó durante Phases 1-2 y se reutilizó sin
> cambios estructurales al cambiar de estrategia.

- Session 1 (Steps 1-4) complete. Foundation: scaffolding, config, MT5 connection, data fetching.
- Quality review done after Step 4: fixed fragile default_factory access in config.py, switched main.py from print to logging.
- Step 5 complete. drift/indicators.py: compute_rsi, compute_atr, compute_adx, compute_all. Nota: durante esta fase se implementaron también EMA/MACD como parte de iteraciones previas (ver D029); esas funciones quedaron como código muerto a eliminar en el Step 6 de Phase 2.5.
- Step 6 complete. Backtest de validación rápido con datos sintéticos o de MT5 para confirmar mérito de la estrategia antes de invertir tiempo en demo.
- Session 2 (Steps 5-7) complete. Indicadores, backtest de validación, lógica de estrategia (iteraciones previas al Daily Lull Scalper; ver historial en D029).
- Quality review after Step 7: fixed fragile column access in indicators.py, removed dead code branch in strategy.py, added frozen=True to Signal dataclass.
- Step 8 complete. drift/risk.py implements calculate_position_size, check_max_trades, check_correlation, check_drawdown, check_all_risk.
- Step 9 complete. drift/executor.py implements open_trade, close_trade, modify_sl, get_open_positions. Uses mt5.order_send with TRADE_ACTION_DEAL for open/close and TRADE_ACTION_SLTP for SL modification. get_open_positions filters by magic number and returns UTC datetimes.
- Session 3 (Steps 8-10) complete. Risk, execution, trailing stop (implementado pero desactivado en Daily Lull Scalper).
- Quality review after Step 10: fixed close_trade magic=0 bug (now passes magic through), unified _count_selling/_count_buying into single _count_currency_exposure helper.
- Session 4 (Steps 11-13) complete. Database, Telegram, main loop.
- Quality review after Step 13 (integration milestone): fixed 3 bugs in main.py:
  1. SL/TP calculated with entry_price=0.0 then sent to MT5 → now get tick price first, compute SL/TP, then open_trade
  2. close_reason inferred from P&L sign → now uses MT5 DEAL_REASON_SL/DEAL_REASON_TP
  3. duration_minutes=0 in all close notifications → now reads actual duration from DB after close_trade_record
- Step 14 complete. Safety systems audit + hardening:
  - Most systems were already correctly wired (drawdown+/resume, Telegram error guards, MT5 reconnect, per-pair error isolation).
  - Fixed: risk-rejected signals now logged as decision=rejected with "risk: <reason>" instead of incorrectly using the strategy reason string. Added rejection_reason param to log_signal() in db.py.
  - Added: _validate_pairs() called at startup — logs warnings for any configured pair that is missing or not visible in MT5.
  - Added: inner try/except in main loop — unexpected exceptions pause the bot, send a Telegram alert, and retry after 60s instead of crashing.
  - Nota: el "cierre de viernes" de la iteración anterior (D026) no aplica al Daily Lull Scalper — las sesiones de viernes se omiten íntegramente y no hay cierre parcial.
- Session 5 (Steps 14-16) complete. Safety hardening, weekly report, E2E tests.
- Final quality review: extracted duplicated _format_duration/_pnl_str into drift/formatting.py (shared by telegram_bot.py and report.py).
- User docs (docs/user/) fully written: getting-started, configuration, commands.
- **Phase 1 MVP complete.** 16 steps, 46 tests (39 pass, 7 skip on Python 3.14).
- Session 6 (Steps 17-19) complete. Motor de backtest para Daily Lull Scalper, descarga de datos históricos, backtest masivo sobre los pares objetivo.
- Quality review: extracted shared indicator functions to backtest/_indicators.py, refactored validate_strategy.py to reuse lull_engine.py's DailyLullStrategy and prepare_backtest_data.
- Backtest results with synthetic data show strategy needs real MT5 data for meaningful evaluation.
- Session 7 (Steps 20-21) complete. Comparación de pares y optimización de parámetros (walk-forward, sin overfitting).
- Resultados de optimización: EURCHF PF 8.17, AUDNZD PF 4.29 sobre 2 años de datos M15 reales (ver D029 para tabla completa y parámetros universales finales).
- USDJPY descartado por empirismo: PF 0.75 con parámetros universales (ver D029). Pares finales: AUDNZD, EURCHF, EURJPY, GBPJPY, EURGBP.
- **Phase 2 complete.** Infraestructura de backtest lista; estrategia activa: Daily Lull Scalper M15.

### Mantenimiento 2026-06-02 — auditoría, zonas horarias y ejecución (demo en marcha)
- **Auditoría de estrategia y docs.** Confirmado que el código sigue fielmente la spec (D029); el "desfase" era documental, no de ejecución. Corregidos en 14 archivos: etiquetas de zona horaria (UTC/GMT → hora servidor MT5 GMT+3), prosa de parámetros pre-optimización en el knowledge doc (→ 4.0x / ADX 35 / RSI 35-65 / SL 2.5x), columnas EMA/MACD fantasma en ROADMAP/ARCHITECTURE, `check_tp_hit` muerta, pares 6→5, datos operativos (server name, log 10MB, ruta DB, drawdown). `fixes-audit.md` archivado.
- **D039 — arquitectura de zonas horarias.** Tres capas: calcular sesión en hora servidor (intacto), guardar en UTC real, mostrar en Bogotá (UTC-5 configurable vía `reports.timezone`). Fix de borde: `db.log_signal` convierte server→UTC al persistir; `formatting.format_time` lee la zona. Corrige señales que se guardaban +3h. Lógica de sesión y reporte semanal sin tocar.
- **D040 — ejecución robusta en rollover.** `open_trade` reintenta ante retcodes transitorios (10018 market closed del rollover 00:00 servidor, etc.) hasta `system.order_retry_attempts`×`order_retry_delay_seconds` (3×25s), con **guardia de precio** que abandona el reintento si el bid revirtió fuera del extremo (no persigue entradas tardías degradadas). Origen: señal válida de EURJPY perdida el 2026-06-02 por retcode 10018.
- Tests: 113 pasan (nuevos `test_timezones.py` y `test_executor.py`). Bot demo reiniciado para cargar los tres fixes; prueba real en la sesión completa del día siguiente.
- Pendientes anotados: verificar stack pandas (`fixes-audit.md` paso 3), config "muerta" `session_start_hour`/`range_definition_hours` (hardcodeada en strategy/main — refactor junto al pipeline multi-sesión D038).

### Mantenimiento 2026-06-02/03 — zonas horarias DST, renombrado y validación de métricas
- **D041 — auditoría DST.** Descubierto que la afirmación "GMT+3 fijo, sin DST" (D037/D039) era **incorrecta**: ICMarkets es GMT+2 invierno / GMT+3 verano, anclado al cierre de NY (doc oficial). El barrido sigue válido (las velas estampadas por el servidor están ancladas a NY → "21:00 servidor" es el mismo momento todo el año). Investigado además que la "sesión asiática" es en realidad el *daily lull* cierre-NY → pre-Tokio (corroborado con fuentes externas). Código: `get_server_utc_offset` con fallback DST-aware (`_us_dst_active`) y re-derivación del offset al inicio de cada sesión en `main.py`. Comentarios falsos "sin DST" corregidos.
- **D042 — renombrado Asian Session Scalper → Daily Lull Scalper.** El nombre era geográficamente incorrecto. Renombrado en todo: clase `AsianSessionStrategy`→`DailyLullStrategy`, archivos `asian_engine/run_asian/optimize_asian.py`→`lull_*`, `ASIAN_PAIRS`→`LULL_PAIRS`, reason strings, `results_asian*/`→`results_lull*/`, doc `daily-lull-scalper.md`. Sin cambios de lógica/params/schema. 113 tests verdes. DECISIONS.md conserva el nombre viejo en entradas históricas.
- **D043 — reconciliación de métricas.** Los números de D029 (EURCHF "PF 8.17", 277 trades) **no se reproducían** — venían de params más restrictivos (defaults planos pre-D033). Re-descargados los 6 pares de MT5, re-corrido el backtest, regenerados todos los `results_lull/*.json`. Números reproducibles reales (params universales): EURCHF 4.14, AUDNZD 5.83, portfolio **+8.75% / PF ~3.0 / DD -0.74%** sobre 5 pares vivos. Edge válido pero menor que lo anunciado. Limitación documentada: el broker solo tiene M15 de AUDNZD desde 2025-01-02 (~1.4a).
- **Validación out-of-sample** (`backtest/validate_oos.py`, nuevo). Split cronológico IS 70%/OOS 30%. Walk-forward (optimiza solo en IS, prueba en OOS no visto): **5/5 pares rentables OOS, retención media de PF 1.12, portfolio OOS +2.92%**. El edge generaliza, no hay colapso. Los params óptimos de IS caen cerca de los universales (EURCHF idéntico). Caveat: AUDNZD el más débil (retención 0.46, sigue rentable).
- Pendientes anotados: (a) AUDNZD con ventana de datos corta — ponderar su +16% con cuidado, completar historia si el broker la ofrece; (b) config "muerta" `session_start_hour`/`range_definition_hours` (sigue del bloque anterior).

### Mantenimiento 2026-06-03 — rollover y fidelidad vivo↔backtest (D044, D045)
- **Disparador:** en demo, las 2 únicas señales aceptadas (2-jun EURJPY, 3-jun EURCHF) cayeron en la vela **00:00 servidor** (el rollover diario del broker). El 3-jun el reintento de D040 abrió EURCHF pero **llenó en el gap de reapertura** (señal 0.91604, fill 0.92026, TP por debajo del fill) → cerró por time-stop en **-$29.24** sobre una señal que habría sido ganadora.
- **Investigación** (`backtest/analyze_entry_hours.py`, `analyze_exclude_rollover.py`, nuevos): el backtest concentra **92% del PnL en la hora 00 y 88% win en la vela 00:00 exacta**. PERO el backtest evalúa esa vela en su **cierre (00:15, post-rollover, limpio)**; el vivo la evaluaba **en formación a las 00:00:05 (durante el halt)**. Excluyendo la vela 00:00, el edge **sobrevive**: +6.41%, PF 1.8-5.7 → **es real, no un artefacto**; el problema era de ejecución/fidelidad.
- **D045 — evaluar velas CERRADAS** (la raíz). `drift.strategy.closed_bars` filtra `index < bar_close_time` en `_analyse_pair_m15`; el bot deja de actuar sobre la vela en formación. La vela 00:00 se evalúa en el wake de las 00:15 (cerrada, limpia) → reproduce el backtest. Única divergencia: se pierde la entrada de la bar 01:45 (+0.49 sobre 2a, despreciable). Scheduler sin cambios.
- **D044 — retraso de ejecución del wake 00:00** fuera del halt (`system.rollover_settle_seconds`, 150s). Complementaria a D045 (señal sobre vela cerrada) y D040 (reintento de respaldo). Corregido: D044 NO quedó obsoleta.
- Tests: **119 pasan** (nuevos `test_scheduling.py`, `test_closed_bars.py`). Bot reiniciado para cargar D041+D044+D045; **prueba real: sesión completa del 4-jun** (¿el vivo reproduce el backtest?).
- Pendiente clave a vigilar: confirmar en vivo que con velas cerradas las entradas y fills coinciden con el backtest; si el edge realizable post-fix es el ~+6.41% (sin la vela 00:00 irrealizable) y no el +8.75%, ajustar expectativas.

### Mantenimiento 2026-06-04 — guardia de spread (D046)
- **Primera sesión con D044+D045 activos.** Las 3 señales se evaluaron correctamente sobre velas cerradas (wake 00:15) y sin retcode 10018 — D044/D045 funcionaron. PERO las 3 entradas (EURJPY, GBPJPY, EURGBP) **gapearon por encima de su propio TP** y las 3 cerraron en pérdida: −$12.37, −$8.89 (¡por `take_profit`!), −$4.85 = **−$26.11**. Balance 1969.84 → 1958.25.
- **Causa raíz (capa que faltaba):** señal/TP/SL se calculan sobre el **cierre (bid)**, pero la orden a mercado llena al **ask**. A las 00:15 servidor (15 min post-rollover) los spreads JPY siguen inflados (10-20 pips); como los TP de mean reversion son ~6 pips, el spread solo deja el fill pasado el TP → "take profit" que es pérdida. El backtest llena al open sin spread, por eso nunca lo vio.
- **D046 — guardia de reward-tras-spread.** `executor.open_trade` abandona la entrada si tras pagar el spread vivo sobrevive < `system.min_reward_fraction` (default 0.5) del reward `|tp - entry_reference|`. Las 3 entradas del 4-jun se habrían rechazado. Cuarta capa sobre D045 (señal)+D044 (timing)+D040 (retry).
- Tests: **123 pasan** (nuevo `TestRewardAfterSpreadGuard` en `test_executor.py`). Falta reiniciar el bot para cargar D046.
- Pendiente: re-correr el backtest con **modelo de spread realista** (inflado en la hora del rollover) para revalidar si el edge +6.41%/+8.75% sobrevive al costo real de ejecución.

### Mantenimiento 2026-06-07 — offset de servidor con mercado cerrado (D047)
- **Disparador:** al reiniciar el bot un domingo (mercado cerrado) para cargar D046, derivó offset **UTC-42** (`raw=-151086s`) y programó sleep de ~45h. Causa: `symbol_info_tick` devuelve el último tick del **viernes** (viejo, no `None`), y el fallback solo cubría `tick is None`.
- **D047 — guardia de plausibilidad.** `get_server_utc_offset` rechaza `|raw| > 14h` y cae al fallback DST-aware (UTC+2/+3). Reiniciar en cualquier momento (incl. fin de semana) ahora da offset correcto; se re-deriva con tick fresco al inicio de cada sesión.
- Balance al reinicio: **1939.59** (bajó de 1958.25 — el bot operó con código viejo, sin D046, en las sesiones 5/6-jun). Las pérdidas de esas sesiones quedan por revisar.
- Tests: **126 pasan** (nuevo `TestServerOffsetStaleTickGuard`). Bot reiniciado con D046+D047 activos.

### Mantenimiento 2026-06-08 — fidelidad de balance_at_close (D048)
- **Auditoría 5/6-jun:** NO hubo trades esos días (viernes/sábado se saltan por diseño; solo reconexiones de fin de semana). La caída 2000→1939.59 está 100% explicada por los 4 trades conocidos (−$55.35 P&L + ~$5 comisiones). No hay pérdidas fantasma.
- **Bug destapado:** el campo `balance_at_close` de la DB era un **snapshot rezagado** (los 3 trades del 4-jun marcaron 1958.25 cuando el liquidado real era ~1939.59). Me llevó a un diagnóstico inicial equivocado.
- **D048 — leer balance liquidado fresco.** `_close_session_trades` y `_detect_closed_trades` ahora persisten `get_balance()` al cierre en vez del snapshot previo; eliminado el parámetro `balance` muerto de ambas. `profit_loss` por trade siempre fue correcto.
- Tests: **127 pasan** (nuevo `test_balance_at_close_is_settled_balance`). Cambio solo afecta trades NUEVOS; el bot toma D048 en su próximo reinicio (no urgente).
- **Siguiente:** backtest con modelo de spread realista (inflado en rollover) para revalidar el edge.

### Análisis 2026-06-08 — backtest con spread realista (`analyze_spread_cost.py`)
- **Motivación:** el backtest llena al Open (velas = bid) y solo cobra comisión plana — nunca paga el spread, y menos el **blowout del rollover** (donde está ~92% del profit idealizado, D045). Una compra viva paga el ASK = bid + spread completo.
- **Modelo:** spreads ICMarkets raw (normal + inflado en ventana rollover 00:00-00:29), anclados a los gaps reales del 04-jun (EURJPY ~10, GBPJPY ~13, EURGBP ~5 pips). Se cobra solo el **exceso** sobre lo normal (la comisión baseline ~1.5 pips ya cubre condiciones normales).
- **Resultado (5 pares, ~1.4-2 años):**
  - Gross idealizado: **+8.75%**
  - Modelo CENTRAL (exceso rollover): **+5.11%** (sobrevive 58%)
  - Cota PESIMISTA (spread completo en todo): **+3.10%** (sobrevive 35%)
  - **47% de los trades caen en la ventana rollover**; su exceso de spread se come ~$79 de $150 brutos.
- **Conclusión:** el edge **es real pero ~la mitad** de lo anunciado: realista **+3% a +5%** sobre el período, no +8.75%. Caveat: depende de los supuestos de spread (3 observaciones vivas). **D046 no está en este modelo** — en vivo, su guardia rechaza las entradas rollover donde el spread se come >50% del TP (las peores), así que el realizado-con-D046 debería ubicarse en o por encima del central.
- Ajustar expectativas para go-live: pensar en **low-single-digit anual**, no ~9%.

### Mantenimiento 2026-06-08 — arranque resiliente (D049)
- **Disparador:** al reiniciar para cargar D048, un fallo DNS transitorio (router 192.168.80.1 caído; resolución de nombres caída pero ruteo por IP OK) en el arranque de Telegram tiró el proceso con `httpx.ConnectError: getaddrinfo failed`. El handler FATAL decía "NSSM should restart" pero corre por Task Scheduler (sin auto-restart) → **bot quedó caído**.
- **D049 — `_setup_telegram_resilient`.** Reintenta setup+initialize de Telegram con backoff (10 intentos, ~7 min de tolerancia) antes de rendirse; Telegram es plano de control, no dependencia de trading. Corregidas referencias obsoletas a NSSM → Task Scheduler.
- Tests: **129 pasan** (nuevo `test_startup_resilience.py`).
- **DNS:** recuperado; bot reiniciado limpio (Telegram registró 8 comandos, offset UTC+3, durmiendo hasta 09-jun 21:00 servidor). D046+D047+D048+D049 todos live.
- **Task Scheduler "Drift" reforzada (2ª capa de robustez):** ya tenía restart-on-failure 3×1min — insuficiente (los 3 reintentos cayeron contra el mismo DNS muerto en ~3 min y se agotaron). Ahora **RestartCount=5, RestartInterval=PT2M, StartWhenAvailable=True** (resto intacto: ExecutionTimeLimit ilimitado, MultipleInstances=IgnoreNew). Combinado con la ventana de ~7 min de reintento interno de D049, tolera apagones de red prolongados.

### Mantenimiento 2026-06-10 — Step 36 validado en vivo + fix de offset frío (D065)
- **Step 36 completo:** desplegado Phase 2.6+2.7 en demo (restart limpio, `baseline_capital` migrado, seeding D062 OK). Paridad backtest bit-exact (4/4) + sesión en vivo: 5/5 rangos lockeados, filtro ADX, 2 fills (EURJPY/GBPJPY), 5 rechazos D046, close_all en el time-stop (D064), reset + sleep. P&L −25.24 (ruido de 1 sesión).
- **Bug cazado y parcheado en vivo (D065):** arranque frío de MT5 → `get_server_utc_offset` derivó UTC-5 (tick ~8h rancio, dentro del ±14h de D047) → envenenó el primer sleep → se habría saltado la sesión. **Fix:** guard estricto `{+2,+3}` (D041) en `get_server_utc_offset` + `_refresh_server_offset` antes de computar el wake. Tests en `test_timezones.py`. Rama `fix/offset-cold-start`. Pre-existente, no regresión de 2.7.
- **Phase 3 (go-live)** diferida deliberadamente: acumular trades 1-2 meses antes del VPS/live.

### Auditoría 2026-06-09 — modelo de riesgo por estrategia (→ Phase 2.7, D062–D064)
- **Disparador:** auditoría del núcleo de la Phase 2.6 (engine/strategy/risk/config/db/telegram/main). Suite 290 passed / 2 skipped, ruff limpio — los hallazgos son de **corrección de lógica de riesgo**, no crashes.
- **Hallazgo raíz (#1):** la equity por estrategia **cuenta el realized dos veces** (`engine._check_strategy_drawdown` + `main._check_strategy_drawdown_pause`): `baseline = balance_vivo × alloc` ya incluye el realized, y `strategy_equity()` lo re-suma → `equity = inicial + 2·realized + floating`. Freno por estrategia ~2× demasiado sensible al 100%; **contaminación cruzada** entre estrategias en multi-estrategia (rompe el aislamiento de D053).
- **Consecuencias:** #3 (peak monótono que no resetea), #4 (display de drawdown en Telegram con una 3ª fórmula incoherente: peak inflado vs balance crudo), #2 (`/resume <estrategia>` inútil: el monitor re-pausa en ≤30s).
- **Otros:** #5 (`get_stats(strategy=)` devuelve peak global), #6 (time-stop 02:00 no corre bajo pausa global), #7 (notif "SESSION CLOSED 0 positions" cada noche), #8 (retries ~75s bajo `_trade_lock` bloquean el monitor), #9 (log spam por par), #10 (`closed_bars` muerto en prod), #11 (engine importa pip helpers desde main), + código muerto (`check_all_risk`, `StrategyConfig`).
- **Plan (sesión /spec):** Phase 2.7 Steps 37-42, decisiones D062 (baseline persistido), D063 (reset de peak en `/resume`), D064 (pausa bloquea solo aperturas). Reset del peak global diferido. Empezar por Step 37.

### Diseño 2026-06-08 — Plataforma multi-estrategia (sesión /spec, D050–D057)
- **Pivote planeado:** Drift pasa de bot mono-estrategia a plataforma multi-estrategia (D050). El framework se construye **antes** del go-live (Phase 2.6), en paper (HC4). Daily Lull = instancia #1.
- **Decisiones:** D050 (pivote/secuencia), D051 (contrato `on_bar` + motor reloj-por-suscripción), D052 (magic base+offset), D053 (riesgo dos niveles + allocations ≤100% + pausa mantiene posiciones), D054 (config `strategies[]` anidado, migración manual), D055 (backtest unificado, portfolio diferido), D056 (DB `strategy` + `strategy_state`), D057 (Telegram por estrategia).
- **Artefactos:** ROADMAP Phase 2.6 (steps 27-36), DECISIONS D050-D057, ARCHITECTURE actualizado, `docs/knowledge/strategy-framework.md` (nuevo), docs/user (configuration/commands) actualizados, CLAUDE.md actualizado.
- **Pendiente de implementación:** todo (esto fue solo diseño). Empezar por Step 27.

- **Secuencia recomendada:** Phase 2.6 (Steps 27-36, multi-estrategia) **antes** de Phase 3 (Step 22, Go Live). Next session starts at **Step 27** (Phase 2.6: Contrato Strategy).
