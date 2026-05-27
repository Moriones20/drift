# Configuración

## config.yaml

Toda la configuración de Drift está en un solo archivo `config.yaml`.

### Broker

<!-- Documentar campos de broker -->

### Estrategia

<!-- Documentar parámetros de estrategia -->
- `timeframe_trend`: Timeframe para filtro de tendencia (D1)
- `timeframe_entry`: Timeframe para señal de entrada (H4)
- `ema_fast`: Período EMA rápida (50)
- `ema_slow`: Período EMA lenta (200)
- `macd`: Parámetros MACD [fast, slow, signal] (12, 26, 9)
- `atr_period`: Período ATR (14)

### Gestión de riesgo

- `percent_per_trade`: Riesgo por trade (1.0%)
- `max_open_trades`: Máximo trades simultáneos (4)
- `max_drawdown_percent`: Drawdown máximo antes de pausa (10%)
- `trailing_stop_atr_multiplier`: Multiplicador ATR para stop loss (1.5)
- `take_profit_ratio`: Ratio de take profit (2.0 = 1:2)

### Pares

<!-- Documentar cómo agregar/quitar pares -->

### Telegram

<!-- Documentar bot_token y chat_id -->

### Reportes

- `timezone`: Zona horaria para reportes (UTC-5)
- `weekly_report_day`: Día del reporte semanal (sunday)
- `weekly_report_hour`: Hora del reporte (20 = 8pm)
