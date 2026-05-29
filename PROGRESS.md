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
- [x] 7. Módulo de estrategia — lógica inicial de estrategia (iteraciones previas al Asian Scalper)

### Session 3: Risk + Execution (Steps 8-10)
- [x] 8. Módulo de riesgo — position sizing, límites (4 trades, 2 por moneda), drawdown, comisiones
- [x] 9. Módulo de ejecución — abrir/cerrar trades, SL/TP
- [x] 10. Módulo de trailing stop — implementado; desactivado en Asian Scalper (use_trailing_stop: false)

### Session 4: Database + Telegram (Steps 11-13)
- [x] 11. Base de datos SQLite — schema, CRUD trades/señales/eventos
- [x] 12. Módulo de Telegram — bot, comandos, notificaciones
- [x] 13. Loop principal — orquestador M15, ciclo completo (ventana 21:00-02:00 UTC)

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
- [ ] 1. DB: añadir `session_close` y quitar `friday_close` del CHECK + migración rebuild de `trades`
- [ ] 2. Cierre de sesión 02:00 bajo `_trade_lock` y aislado por posición (try/except)
- [ ] 3. `_detect_closed_trades`: motivo desconocido ⇒ `manual`, no `trailing_stop`

### Batch B — Medio
- [ ] 4. Reporte semanal derivado de `reports.*` config (no hardcode lunes 01:00 UTC)
- [ ] 5. Paridad backtest⇄vivo: defaults de clase = D029 + run_asian pasa los 6 params desde config

### Batch C — Limpieza
- [ ] 6. Eliminar config/código muerto (friday_close_hour_utc, take_profit_*, calculate_sl_tp, check_tp_hit, compute_* sin uso, campos legacy de StrategyConfig)
- [ ] 7. `DEFAULT_PAIRS` = los 5 pares de D029
- [x] 8. Sincronizar docs (reescribir PROGRESS legacy, barrer ARCHITECTURE/docs por friday_close/EMA/MACD)
- [ ] 9. Tests de regresión del ciclo de sesión (session_close, cierre aislado, weekly trigger, migración)

## Phase 3 — Live

### Session 8: Go Live (Steps 22-26)
- [ ] 22. Migrar a VPS Windows
- [ ] 23. Configurar cuenta live ICMarkets
- [ ] 24. Ajustar config.yaml con credenciales live
- [ ] 25. Monitoreo intensivo primera semana
- [ ] 26. Operación autónoma con journal dominical

---

## Notes

> **Pivote de estrategia (D029):** La estrategia activa es **Asian Session Scalper M15**
> (ver `CLAUDE.md`). Las iteraciones anteriores (trend-following EMA/MACD, mean-reversion H4)
> fueron el camino hasta llegar aquí — ver D029 para el historial completo. La infraestructura
> (risk/executor/db/telegram/main loop) se construyó durante Phases 1-2 y se reutilizó sin
> cambios estructurales al cambiar de estrategia.

- Session 1 (Steps 1-4) complete. Foundation: scaffolding, config, MT5 connection, data fetching.
- Quality review done after Step 4: fixed fragile default_factory access in config.py, switched main.py from print to logging.
- Step 5 complete. drift/indicators.py: compute_rsi, compute_atr, compute_adx, compute_all. Nota: durante esta fase se implementaron también EMA/MACD como parte de iteraciones previas (ver D029); esas funciones quedaron como código muerto a eliminar en el Step 6 de Phase 2.5.
- Step 6 complete. Backtest de validación rápido con datos sintéticos o de MT5 para confirmar mérito de la estrategia antes de invertir tiempo en demo.
- Session 2 (Steps 5-7) complete. Indicadores, backtest de validación, lógica de estrategia (iteraciones previas al Asian Scalper; ver historial en D029).
- Quality review after Step 7: fixed fragile column access in indicators.py, removed dead code branch in strategy.py, added frozen=True to Signal dataclass.
- Step 8 complete. drift/risk.py implements calculate_position_size, check_max_trades, check_correlation, check_drawdown, check_all_risk.
- Step 9 complete. drift/executor.py implements open_trade, close_trade, modify_sl, get_open_positions. Uses mt5.order_send with TRADE_ACTION_DEAL for open/close and TRADE_ACTION_SLTP for SL modification. get_open_positions filters by magic number and returns UTC datetimes.
- Session 3 (Steps 8-10) complete. Risk, execution, trailing stop (implementado pero desactivado en Asian Scalper).
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
  - Nota: el "cierre de viernes" de la iteración anterior (D026) no aplica al Asian Scalper — las sesiones de viernes se omiten íntegramente y no hay cierre parcial.
- Session 5 (Steps 14-16) complete. Safety hardening, weekly report, E2E tests.
- Final quality review: extracted duplicated _format_duration/_pnl_str into drift/formatting.py (shared by telegram_bot.py and report.py).
- User docs (docs/user/) fully written: getting-started, configuration, commands.
- **Phase 1 MVP complete.** 16 steps, 46 tests (39 pass, 7 skip on Python 3.14).
- Session 6 (Steps 17-19) complete. Motor de backtest para Asian Session Scalper, descarga de datos históricos, backtest masivo sobre los pares objetivo.
- Quality review: extracted shared indicator functions to backtest/_indicators.py, refactored validate_strategy.py to reuse asian_engine.py's AsianSessionStrategy and prepare_backtest_data.
- Backtest results with synthetic data show strategy needs real MT5 data for meaningful evaluation.
- Session 7 (Steps 20-21) complete. Comparación de pares y optimización de parámetros (walk-forward, sin overfitting).
- Resultados de optimización: EURCHF PF 8.17, AUDNZD PF 4.29 sobre 2 años de datos M15 reales (ver D029 para tabla completa y parámetros universales finales).
- USDJPY descartado por empirismo: PF 0.75 con parámetros universales (ver D029). Pares finales: AUDNZD, EURCHF, EURJPY, GBPJPY, EURGBP.
- **Phase 2 complete.** Infraestructura de backtest lista; estrategia activa: Asian Session Scalper M15.
- Next session starts at Step 22 (Phase 3: Go Live).
