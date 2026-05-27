# Drift — Progress Tracker

## Phase 1 — MVP (Demo)

### Session 1: Foundation (Steps 1-4)
- [ ] 1. Scaffolding del proyecto — estructura, venv, dependencias
- [ ] 2. Módulo de configuración — config.yaml loader y validación
- [ ] 3. Módulo de conexión MT5 — conectar, desconectar, health check, reconexión
- [ ] 4. Módulo de datos — obtener velas D1 y H4 de MT5

### Session 2: Strategy + Validation (Steps 5-7)
- [ ] 5. Módulo de indicadores — EMA 50/200, MACD, ATR
- [ ] 6. Backtest de validación — backtest rápido en EURUSD y GBPUSD para confirmar mérito de la estrategia
- [ ] 7. Módulo de estrategia — lógica trend following D1+H4

### Session 3: Risk + Execution (Steps 8-10)
- [ ] 8. Módulo de riesgo — position sizing, límites (4 trades, 2 por moneda), drawdown, comisiones
- [ ] 9. Módulo de ejecución — abrir/cerrar trades, SL/TP
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

_Espacio para blockers, decisiones tomadas durante implementación, y cosas a revisar._
