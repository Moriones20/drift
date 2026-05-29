# Drift — Architecture

## System Diagram

```
┌─────────────────────────────────────────────────────┐
│                   MT5 Terminal                       │
│              (ICMarkets Raw Spread)                  │
│   Pares: AUDNZD EURCHF EURJPY GBPJPY EURGBP        │
└────────┬────────────────────────┬────────────────────┘
         │ market data (M15 + H4) │ order execution
         ▼                        ▲
┌────────────────┐        ┌───────────────┐
│  mt5_client.py │        │ executor.py   │
│                │        │               │
│ • connect()    │        │ • open_trade()│
│ • disconnect() │        │ • close_trade()│
│ • get_candles()│        │ • modify_sl() │
│ • get_balance()│        │ • get_open()  │
│ • health_check │        │               │
└───────┬────────┘        └───────▲───────┘
        │                         │
        ▼                         │
┌────────────────┐        ┌───────┴───────┐
│ indicators.py  │        │  risk.py      │
│                │        │               │
│ • rsi(14) M15  │        │ • position_   │
│ • atr(14) M15  │        │   size()      │
│ • adx(14) H4   │        │ • check_max   │
│                │        │   _trades()   │
└───────┬────────┘        │ • check_      │
        │                 │   drawdown()  │
        ▼                 └───────▲───────┘
┌────────────────────────┐        │
│ strategy.py            │────────┘
│                        │
│ • evaluate_pair()      → Signal (buy/sell/none)
│ • update_session_state()→ SessionState per pair
│ • should_close_on_time()→ true at 02:00 GMT
│ • check_tp_hit()       → TP at range midpoint
│                        │
│ SessionState (per pair):│
│   session_date, high,  │
│   low, locked,         │
│   range_high/low,      │
│   traded               │
└───────┬────────────────┘
        │
        ▼
┌────────────────┐        ┌───────────────┐
│ trailing.py    │        │    db.py       │
│ (no-op —       │        │               │
│  trailing       │        │ • log_trade() │
│  disabled)      │        │ • log_signal()│
│                │        │ • log_event() │
│                │        │ • get_stats() │
└────────────────┘        └───────▲───────┘
                                  │
┌─────────────────────────────────┤
│         main.py (Scheduler)     │
│                                 │
│ 4-state machine (UTC):          │
│  A — Outside (02:00-20:59):     │
│      Long sleep until 21:00.    │
│      Stop-aware (10s slices).   │
│      Skip Friday entirely.      │
│                                 │
│  B — Define range (21:00-22:59):│
│      Each M15 close: fetch M15  │
│      + H4, update SessionState  │
│      high/low. No entries.      │
│                                 │
│  C — Trading (23:00-01:59):     │
│      Each M15 close: full       │
│      _analyse_pair_m15() eval.  │
│      Max 1 trade/pair/session.  │
│                                 │
│  D — Session close (02:00):     │
│      Close any open session     │
│      trades, reset SessionState,│
│      transition to A.           │
│                                 │
│ • Continuo (daemon thread):     │
│   MT5 health + reconnect        │
│   Balance / peak / drawdown     │
│   Detect closed trades          │
│   Weekly report check           │
└─────────────┬───────────────────┘
              │
              ▼
┌─────────────────────────────────┐
│       telegram_bot.py           │
│                                 │
│ Notificaciones automáticas:     │
│ • Trade abierto/cerrado         │
│ • Sesión iniciada/cerrada       │
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

## Data Flow — Ciclo de análisis (cada M15 dentro de ventana 21:00-02:00 UTC)

```
Cierre vela M15 (dentro de ventana activa)
      │
      ▼
Para cada par en config.pairs:
  _analyse_pair_m15(pair, session_state)
      │
      ├─→ Obtener últimas ~100 velas M15 + ~30 velas H4
      │
      ├─→ Actualizar SessionState
      │     │
      │     ▼
      │   update_session_state(state, bar_time, bar_high, bar_low, atr)
      │   • Estado B (21:00-22:59): acumular high/low del rango. Sin entradas.
      │   • Estado C (23:00-01:59): rango ya lockeado — evaluar señales.
      │
      ├─→ [solo en Estado C] Evaluar señal
      │     │
      │     ▼
      │   ¿Rango válido? (1.0x-4.0x ATR) ──No──→ LOGGEAR "range invalid" → siguiente par
      │     │Sí
      │     ▼
      │   ¿ADX H4 < 35.0? ──No──→ LOGGEAR "adx filter" → siguiente par
      │     │Sí
      │     ▼
      │   ¿Precio toca extremo del rango?
      │     ├─ BUY: precio <= range_low AND RSI M15 < 35.0
      │     └─ SELL: precio >= range_high AND RSI M15 > 65.0
      │     │
      │     ▼ (si hay señal)
      │   ¿traded == False? ──No──→ LOGGEAR "already traded" → siguiente par
      │     │Sí
      │     ▼
      │   ¿Drawdown < 10%? ──No──→ PAUSAR bot → NOTIFICAR → detener
      │     │Sí
      │     ▼
      │   Calcular position size (1% del balance)
      │   SL = 2.5x ATR(14), TP = range midpoint
      │     │
      │     ▼
      │   Abrir trade en MT5
      │   state.traded = True
      │     │
      │     ▼
      │   LOGGEAR señal aceptada + trade
      │     │
      │     ▼
      │   NOTIFICAR por Telegram
      │
      ├─→ [Estado D — 02:00 UTC] Cerrar trades de sesión abiertos → reset SessionState
      │
      └─→ siguiente par
```

## Data Flow — Monitoreo continuo

```
Cada 30 segundos (thread daemon):
      │
      ├─→ Health check MT5
      │     │
      │     └─→ ¿Desconectado? ──Sí──→ Reintentar cada 5 min → Notificar si falla
      │
      ├─→ Calcular drawdown actual
      │     │
      │     └─→ ¿> 10%? ──Sí──→ Pausar → Notificar
      │
      ├─→ Detectar trades cerrados por MT5 (SL hit, TP hit)
      │     └─→ Loggear → Notificar
      │
      └─→ ¿Domingo 8pm UTC-5? ──Sí──→ Generar y enviar reporte semanal

Nota: trailing stop desactivado (use_trailing_stop: false).
      Cierre por tiempo (02:00 UTC) lo maneja el scheduler principal, no este thread.
      Viernes no se opera (skip-Friday rule en el scheduler).
```

## Main Loop — Arquitectura de threads

```
Thread principal (main.py):
      │
      ├─→ 4-state scheduler (ver diagrama System Diagram):
      │   • Estado A: sleep hasta 21:00 UTC (stop-aware, slices de 10s)
      │   • Estado B/C: esperar próximo cierre M15, analizar 5 pares
      │   • Estado D: cerrar sesión a las 02:00 UTC, reset SessionState
      │   Salta viernes completamente (skip-Friday rule)
      │
      └─→ Thread secundario (daemon):
          Loop cada 30 segundos:
          - MT5 health + reconnect
          - Balance / peak / drawdown
          - Detect closed trades
          - Weekly report check

Thread Telegram (daemon):
      Corre python-telegram-bot polling en thread separado
      Recibe comandos, ejecuta acciones, envía notificaciones
```

## Component Responsibilities

| Componente | Archivo | Responsabilidad |
|---|---|---|
| MT5 Client | `drift/mt5_client.py` | Conexión, datos de mercado, estado de cuenta |
| Indicators | `drift/indicators.py` | Cálculo de RSI, ATR (M15), ADX (H4) |
| Strategy | `drift/strategy.py` | Asian Session Scalper: SessionState, evaluate_pair(), time stop |
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
├── main.py                  # Entry point y loop principal (M15 scheduler)
├── config.yaml              # Configuración (no versionada con credenciales)
├── config.example.yaml      # Ejemplo de configuración (versionado)
├── requirements.txt         # Dependencias Python
├── drift/
│   ├── __init__.py
│   ├── config.py            # Carga y validación de config
│   ├── mt5_client.py        # Conexión y datos de MT5 (M15 + H4)
│   ├── indicators.py        # Cálculos técnicos (RSI, ATR, ADX)
│   ├── strategy.py          # Asian Session Scalper (SessionState, evaluate_pair)
│   ├── risk.py              # Position sizing y gestión de riesgo
│   ├── executor.py          # Ejecución de trades en MT5
│   ├── trailing.py          # Trailing stop manager (desactivado en estrategia actual)
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
│   ├── asian_engine.py                  # Asian Session Scalper (fuente de verdad)
│   ├── run_asian.py                     # Runner del backtest asiático
│   ├── optimize_asian.py                # Optimización de parámetros
│   ├── engine_meanrev_legacy.py         # Mean reversion legacy (solo referencia histórica)
│   ├── run_all_meanrev_legacy.py        # Runner legacy del mean reversion
│   └── results_asian_opt/              # Resultados de optimización
├── docs/
│   ├── DECISIONS.md
│   ├── ARCHITECTURE.md
│   ├── knowledge/
│   │   ├── mt5-python-api.md
│   │   ├── telegram-bot-setup.md
│   │   ├── asian-session-scalper.md    # Estrategia activa — reglas y parámetros
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
    m15_candle_time TEXT NOT NULL,
    range_high REAL,
    range_low REAL,
    range_atr_ratio REAL,
    m15_rsi REAL,
    m15_atr REAL,
    h4_adx REAL,
    session_traded_count INTEGER,
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
