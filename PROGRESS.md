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

- Next session starts at Step 22 (Phase 3: Go Live).
