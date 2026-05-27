# Drift — Architecture

## System Diagram

```
┌─────────────────────────────────────────────────────┐
│                   MT5 Terminal                       │
│              (ICMarkets Raw Spread)                  │
│         Pares: EURUSD GBPUSD USDJPY                 │
│                AUDUSD USDCAD EURGBP                 │
└────────┬────────────────────────┬────────────────────┘
         │ market data            │ order execution
         ▼                        ▲
┌────────────────┐        ┌───────────────┐
│  mt5_client.py │        │ executor.py   │
│                │        │               │
│ • connect()    │        │ • open_trade()│
│ • disconnect() │        │ • close_trade()│
│ • get_candles()│        │ • modify_sl() │
│ • get_balance()│        │ • get_open()  │
│ • health_check│        │               │
└───────┬────────┘        └───────▲───────┘
        │                         │
        ▼                         │
┌────────────────┐        ┌───────┴───────┐
│ indicators.py  │        │  risk.py      │
│                │        │               │
│ • ema(50,200)  │        │ • position_   │
│ • macd(12,26,9)│        │   size()      │
│ • atr(14)      │        │ • check_max   │
│                │        │   _trades()   │
└───────┬────────┘        │ • check_      │
        │                 │   drawdown()  │
        ▼                 └───────▲───────┘
┌────────────────┐                │
│ strategy.py    │────────────────┘
│                │
│ • analyze_pair()  → señal compra/venta/nada
│ • check_d1_trend()→ filtro EMA 50/200
│ • check_h4_entry()→ señal MACD
└───────┬────────┘
        │
        ▼
┌────────────────┐        ┌───────────────┐
│ trailing.py    │        │    db.py       │
│                │        │               │
│ • update_      │        │ • log_trade() │
│   trailing()   │        │ • log_signal()│
│ • check_tp()   │        │ • log_event() │
│                │        │ • get_stats() │
└────────────────┘        └───────▲───────┘
                                  │
┌─────────────────────────────────┤
│         main.py (Loop)          │
│                                 │
│ • Cada cierre de vela H4:       │
│   1. Obtener datos D1/H4        │
│   2. Calcular indicadores       │
│   3. Evaluar estrategia         │
│   4. Verificar riesgo           │
│   5. Ejecutar si hay señal      │
│   6. Loggear decisión           │
│   7. Notificar si corresponde   │
│                                 │
│ • Continuo:                     │
│   8. Monitorear trailing stops  │
│   9. Verificar drawdown         │
│  10. Health check MT5           │
└─────────────┬───────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│       telegram_bot.py           │
│                                 │
│ Notificaciones automáticas:     │
│ • Trade abierto/cerrado         │
│ • Bot inicio/pausa/stop         │
│ • Errores                       │
│ • Reporte semanal (dom 8pm)     │
│                                 │
│ Comandos:                       │
│ /status /trades /history        │
│ /balance /pause /resume         │
│ /stop /report                   │
└─────────────────────────────────┘
```

## Data Flow — Ciclo de análisis (cada 4 horas)

```
Cierre vela H4
      │
      ▼
Para cada par en config.pairs:
      │
      ├─→ Obtener últimas 250 velas D1
      │     │
      │     ▼
      │   Calcular EMA 50 y EMA 200
      │     │
      │     ▼
      │   ¿EMA 50 > EMA 200? ──No──→ ¿EMA 50 < EMA 200? ──No──→ Sin tendencia → LOGGEAR → siguiente par
      │     │Sí                         │Sí
      │     ▼                           ▼
      │   Tendencia ALCISTA           Tendencia BAJISTA
      │     │                           │
      │     └─────────┬─────────────────┘
      │               │
      │               ▼
      │   Obtener últimas 100 velas H4
      │     │
      │     ▼
      │   Calcular MACD (12, 26, 9)
      │     │
      │     ▼
      │   ¿Histograma MACD confirma dirección? ──No──→ LOGGEAR señal rechazada → siguiente par
      │     │Sí
      │     ▼
      │   Calcular ATR(14)
      │     │
      │     ▼
      │   ¿Trades abiertos < 4? ──No──→ LOGGEAR "max trades" → siguiente par
      │     │Sí
      │     ▼
      │   ¿Correlación OK? (max 2 trades misma dirección por moneda) ──No──→ LOGGEAR "correlación" → siguiente par
      │     │Sí
      │     ▼
      │   ¿Drawdown < 10%? ──No──→ PAUSAR bot → NOTIFICAR → detener
      │     │Sí
      │     ▼
      │   Calcular position size (1% del balance, incluyendo comisión)
      │     │
      │     ▼
      │   Calcular SL (1.5x ATR) y TP (2x SL)
      │     │
      │     ▼
      │   Abrir trade en MT5
      │     │
      │     ▼
      │   LOGGEAR señal aceptada + trade
      │     │
      │     ▼
      │   NOTIFICAR por Telegram
      │
      └─→ siguiente par
```

## Data Flow — Monitoreo continuo

```
Cada 30 segundos:
      │
      ├─→ Para cada trade abierto:
      │     │
      │     ├─→ ¿Precio alcanzó TP (1:2)? ──Sí──→ Cerrar → Loggear → Notificar
      │     │
      │     ├─→ Actualizar trailing stop si el precio avanzó a favor
      │     │
      │     └─→ ¿SL alcanzado? (MT5 lo cierra automáticamente) → Detectar cierre → Loggear → Notificar
      │
      ├─→ Calcular drawdown actual
      │     │
      │     └─→ ¿> 10%? ──Sí──→ Pausar → Notificar
      │
      ├─→ Health check MT5
      │     │
      │     └─→ ¿Desconectado? ──Sí──→ Reintentar cada 5 min → Notificar si falla
      │
      └─→ ¿Viernes >= 20:00 UTC? ──Sí──→ Cerrar trades en pérdida → Loggear → Notificar
```

## Main Loop — Arquitectura de threads

```
Thread principal (main.py):
      │
      ├─→ Calcular próximo cierre H4 (00:00, 04:00, 08:00, 12:00, 16:00, 20:00 UTC)
      │   Esperar con sleep hasta cierre + 5 segundos (asegurar datos disponibles)
      │   Ejecutar ciclo de análisis para los 6 pares
      │
      └─→ Thread secundario (daemon):
          Loop cada 30 segundos:
          - Trailing stop updates
          - Drawdown check
          - MT5 health check
          - Friday close check

Thread Telegram (daemon):
      Corre python-telegram-bot polling en thread separado
      Recibe comandos, ejecuta acciones, envía notificaciones
```

## Component Responsibilities

| Componente | Archivo | Responsabilidad |
|---|---|---|
| MT5 Client | `drift/mt5_client.py` | Conexión, datos de mercado, estado de cuenta |
| Indicators | `drift/indicators.py` | Cálculo de EMA, MACD, ATR |
| Strategy | `drift/strategy.py` | Lógica de decisión: ¿comprar, vender, o nada? |
| Risk Manager | `drift/risk.py` | Position sizing, límites de trades, drawdown |
| Executor | `drift/executor.py` | Abrir/cerrar trades, configurar SL/TP |
| Trailing Stop | `drift/trailing.py` | Monitorear y actualizar stops de trades abiertos |
| Database | `drift/db.py` | SQLite CRUD: trades, señales, eventos |
| Telegram | `drift/telegram_bot.py` | Notificaciones y comandos |
| Config | `drift/config.py` | Cargar y validar config.yaml |
| Main Loop | `main.py` | Orquestador principal |

## File Structure

```
drift/
├── main.py                  # Entry point y loop principal
├── config.yaml              # Configuración (no versionada con credenciales)
├── config.example.yaml      # Ejemplo de configuración (versionado)
├── requirements.txt         # Dependencias Python
├── drift/
│   ├── __init__.py
│   ├── config.py            # Carga y validación de config
│   ├── mt5_client.py        # Conexión y datos de MT5
│   ├── indicators.py        # Cálculos técnicos (EMA, MACD, ATR)
│   ├── strategy.py          # Lógica de trend following
│   ├── risk.py              # Position sizing y gestión de riesgo
│   ├── executor.py          # Ejecución de trades en MT5
│   ├── trailing.py          # Trailing stop manager
│   ├── db.py                # SQLite database layer
│   └── telegram_bot.py      # Bot de Telegram
├── data/
│   └── drift.db             # SQLite database (generada en runtime)
├── logs/
│   └── drift.log            # Archivo de log
├── tests/
│   ├── test_indicators.py
│   ├── test_strategy.py
│   ├── test_risk.py
│   └── test_db.py
├── backtest/
│   ├── run_backtest.py      # Script de backtesting
│   └── results/             # Resultados de backtests
├── docs/
│   ├── DECISIONS.md
│   ├── ARCHITECTURE.md
│   ├── knowledge/
│   │   ├── mt5-python-api.md
│   │   ├── telegram-bot-setup.md
│   │   └── trend-following-indicators.md
│   └── user/
│       ├── getting-started.md
│       ├── configuration.md
│       └── commands.md
├── CLAUDE.md
├── ROADMAP.md
└── PROGRESS.md
```

## SQLite Schema

```sql
CREATE TABLE trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,
    direction TEXT NOT NULL CHECK(direction IN ('buy', 'sell')),
    entry_price REAL NOT NULL,
    exit_price REAL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    position_size REAL NOT NULL,
    profit_loss REAL,
    balance_at_open REAL NOT NULL,
    balance_at_close REAL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    close_reason TEXT CHECK(close_reason IN ('trailing_stop', 'take_profit', 'stop_loss', 'manual', 'drawdown_pause')),
    duration_minutes INTEGER,
    mt5_ticket INTEGER
);

CREATE TABLE signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    h4_candle_time TEXT NOT NULL,
    ema_50 REAL,
    ema_200 REAL,
    trend_direction TEXT CHECK(trend_direction IN ('bullish', 'bearish', 'none')),
    macd_value REAL,
    macd_signal REAL,
    macd_histogram REAL,
    atr_value REAL,
    decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
    reason TEXT NOT NULL,
    trade_id INTEGER REFERENCES trades(id)
);

CREATE TABLE bot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_at TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('start', 'stop', 'pause', 'resume', 'error', 'reconnect', 'drawdown_alert')),
    detail TEXT,
    balance REAL
);

CREATE INDEX idx_trades_pair ON trades(pair);
CREATE INDEX idx_trades_opened ON trades(opened_at);
CREATE INDEX idx_signals_pair ON signals(pair);
CREATE INDEX idx_signals_analyzed ON signals(analyzed_at);
CREATE INDEX idx_signals_decision ON signals(decision);
CREATE INDEX idx_events_type ON bot_events(event_type);
```
