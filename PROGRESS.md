# Drift — Progress Tracker

## Phase 1 — MVP (Demo)

### Session 1: Foundation (Steps 1-4)
- [x] 1. Scaffolding del proyecto — estructura, venv, dependencias
- [x] 2. Módulo de configuración — config.yaml loader y validación
- [x] 3. Módulo de conexión MT5 — conectar, desconectar, health check, reconexión
- [x] 4. Módulo de datos — obtener velas D1 y H4 de MT5

### Session 2: Strategy + Validation (Steps 5-7)
- [x] 5. Módulo de indicadores — EMA 50/200, MACD, ATR
- [x] 6. Backtest de validación — backtest rápido en EURUSD y GBPUSD para confirmar mérito de la estrategia
- [x] 7. Módulo de estrategia — lógica trend following D1+H4

### Session 3: Risk + Execution (Steps 8-10)
- [x] 8. Módulo de riesgo — position sizing, límites (4 trades, 2 por moneda), drawdown, comisiones
- [x] 9. Módulo de ejecución — abrir/cerrar trades, SL/TP
- [ ] 10. Módulo de trailing stop — monitoreo y actualización

### Session 4: Database + Telegram (Steps 11-13)
- [ ] 11. Base de datos SQLite — schema, CRUD trades/señales/eventos
- [ ] 12. Módulo de Telegram — bot, comandos, notificaciones
- [ ] 13. Loop principal — orquestador H4, ciclo completo

### Session 5: Safety + Testing (Steps 14-16)
- [ ] 14. Sistemas de seguridad — drawdown, max trades, correlación, cierre de viernes, reconexión, error handling
- [ ] 15. Reporte semanal — generación automática domingos 8pm UTC-5
- [ ] 16. Testing en demo — flujo completo: señal → trade → notificación → logging

## Phase 2 — Backtesting completo

### Session 6: Backtest Setup (Steps 17-19)
- [ ] 17. Integración completa con Backtesting.py
- [ ] 18. Descarga de datos históricos (mín 2 años)
- [ ] 19. Backtest masivo de los 6 pares

### Session 7: Analysis (Steps 20-21)
- [ ] 20. Comparación y ranking de pares
- [ ] 21. Optimización de parámetros (sin overfitting)

## Phase 3 — Live

### Session 8: Go Live (Steps 22-26)
- [ ] 22. Migrar a VPS Windows
- [ ] 23. Configurar cuenta live ICMarkets
- [ ] 24. Ajustar config.yaml con credenciales live
- [ ] 25. Monitoreo intensivo primera semana
- [ ] 26. Operación autónoma con journal dominical

---

## Notes

- Session 1 (Steps 1-4) complete. Foundation: scaffolding, config, MT5 connection, data fetching.
- Quality review done after Step 4: fixed fragile default_factory access in config.py, switched main.py from print to logging.
- Step 5 complete. drift/indicators.py implements compute_ema, compute_macd, compute_atr, compute_all. Note: pandas_ta requires Python <3.14 (numba constraint) — smoke test skipped due to system Python 3.14; will verify in MT5 environment (Windows with Python 3.10).
- Step 6 complete. backtest/validate_strategy.py implements full validation backtest with dual mode (MT5 live data or synthetic fallback). Indicators inlined with plain pandas/numpy to avoid pandas_ta/numba import on Python 3.14. Passes thresholds are printed per-metric; verdict printed at end. Runs clean with ruff.
- Session 2 (Steps 5-7) complete. Strategy core: indicators, validation backtest, strategy logic.
- Quality review after Step 7: fixed fragile MACD column access in indicators.py (positional → named), removed dead code branch in strategy.py, added frozen=True to Signal dataclass.
- Step 8 complete. drift/risk.py implements calculate_position_size, check_max_trades, check_correlation, check_drawdown, check_all_risk, calculate_sl_tp.
- Step 9 complete. drift/executor.py implements open_trade, close_trade, modify_sl, get_open_positions. Uses mt5.order_send with TRADE_ACTION_DEAL for open/close and TRADE_ACTION_SLTP for SL modification. get_open_positions filters by magic number and returns UTC datetimes.
- Next session starts at Step 10 (trailing stop module).
