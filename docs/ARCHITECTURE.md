# Drift — Architecture

> **Plataforma multi-estrategia (D050).** Drift hospeda N estrategias sobre **una sola
> cuenta MT5**. El Daily Lull es la instancia #1. El motor es genérico ("reloj por
> suscripción", D051): no conoce ventanas de sesión, solo cierres de vela. Toda la
> lógica de timing/ventana/estado del Lull (la antigua máquina A/B/C/D) es ahora
> **interna del Lull**, no del motor.

## System Diagram

```
┌─────────────────────────────────────────────────────┐
│                   MT5 Terminal                       │
│              (ICMarkets Raw Spread)                  │
│   Pares: unión de los pairs de las estrategias       │
│          enabled (Lull: AUDNZD EURCHF EURJPY ...)    │
└────────┬────────────────────────┬────────────────────┘
         │ market data (velas)    │ order execution (por magic)
         ▼                        ▲
┌────────────────┐        ┌───────────────┐
│  mt5_client.py │        │ executor.py   │
│                │        │               │
│ • connect()    │        │ • open_trade()│
│ • disconnect() │        │ • close_trade()│
│ • get_candles()│        │ • modify_sl() │
│ • get_balance()│        │ • get_open_   │
│ • health_check │        │   positions(  │
│                │        │   magic)      │
└───────┬────────┘        └───────▲───────┘
        │                         │
        ▼                         │ ejecución + atribución
┌────────────────┐               │ (magic = base+offset)
│  MarketData    │               │
│  (compartido)  │        ┌──────┴────────┐
│ • velas dedup  │        │  risk.py      │
│   por (par,TF) │        │  (DOS niveles)│
│   por tick     │        │               │
└───────┬────────┘        │ Global:       │
        │                 │ • max_open    │
        │                 │ • max_dd      │
        │ market, ctx     │   (kill all)  │
        ▼                 │ • correlación │
┌──────────────────────────┐ Por estrat.: │
│  MOTOR — reloj por        ││ • notional   │
│  suscripción (main.py)    ││   sizing     │
│                           ││ • max_open   │
│  • tick = unión de los    ││ • max_dd     │
│    cierres de vela de los ││   (pausa esa)│
│    timeframes declarados  │└──────▲───────┘
│  • por cada estrategia    │       │
│    enabled & no pausada,  │       │ gating
│    por cada par suyo:     │───────┘
│    on_bar(...) -> Decision│
│  • aplica gating riesgo,  │
│    ejecuta, atribuye,     │
│    loggea, notifica       │
│  • reloj/sleep stop-aware │
│  • threads: monitoreo +   │
│    Telegram               │
└──────┬──────────────┬─────┘
       │ despacha      │ indicators.py (RSI/ATR/ADX)
       ▼               │ lo usan las estrategias
┌──────────────────────────────────────────────┐
│  drift/strategies/                            │
│                                               │
│  base.py — contrato:                          │
│   • class Strategy:                           │
│       name: str                               │
│       timeframes: frozenset[str]   # {"M15"}  │
│       on_bar(pair, timeframe,                 │
│              bar_close_time,                   │
│              market, ctx) -> Decision         │
│   • Decision = Open(signal) | Close(ticket)   │
│              | CloseAll(reason) | NoOp         │
│   • MarketData  (acceso a velas por par/TF)   │
│   • StrategyContext (balance, posiciones,     │
│                      config de la instancia)  │
│   • REGISTRY = {"daily_lull": DailyLull...}   │
│                                               │
│  daily_lull.py — instancia #1:                │
│   • timeframes = {"M15"}  (usa H4 vía market) │
│   • SessionState privado (per par):           │
│       session_date, high, low, locked,        │
│       range_high/low, traded                  │
│   • máquina de ventana INTERNA:               │
│       21:00-22:59 define rango (NoOp)         │
│       23:00-01:59 evalúa señales (Open)       │
│       02:00 time-stop -> CloseAll             │
│   • reglas internas: skip-Friday, espera      │
│     de rollover, magic_offset 0               │
└──────┬────────────────────────────────┬──────┘
       │                                 │
       ▼                                 ▼
┌────────────────┐               ┌───────────────┐
│ trailing.py    │               │    db.py       │
│ (no-op —       │               │               │
│  trailing      │               │ • log_trade(  │
│  disabled)     │               │   strategy)   │
│                │               │ • log_signal( │
│                │               │   strategy)   │
│                │               │ • log_event(  │
│                │               │   strategy?)  │
│                │               │ • strategy_   │
│                │               │   state CRUD  │
└────────────────┘               └───────────────┘

┌─────────────────────────────────┐
│       telegram_bot.py           │
│                                 │
│ Notificaciones automáticas:     │
│ • Trade abierto/cerrado         │
│ • Sesión iniciada/cerrada       │
│ • Bot inicio/pausa/stop         │
│   (global o por estrategia)     │
│ • Errores                       │
│ • Reporte semanal (dom 8pm,     │
│   con desglose por estrategia)  │
│                                 │
│ Comandos:                       │
│ /status /trades /history        │
│ /balance /report  (agregado +   │
│   desglose por estrategia)      │
│ /strategies  (lista cada una)   │
│ /pause [nombre] /resume [nombre]│
│ /stop  (global)                 │
└─────────────────────────────────┘
```

## Data Flow — Ciclo de análisis (por cada tick = cierre de vela en la unión de timeframes)

> El motor NO conoce ventanas. La lógica de ventana del Lull (define-rango,
> trading, time-stop 02:00, skip-Friday, espera de rollover) vive DENTRO de
> `on_bar` de `daily_lull.py`. El motor solo despacha cierres de vela.

```
TICK = próximo cierre de vela en la UNIÓN de los timeframes declarados
       por las estrategias enabled (Lull declara {"M15"})
      │
      ▼
MarketData: fetch de velas DEDUP por (par, timeframe) para este tick
  (si dos estrategias piden el mismo par/TF, se baja UNA sola vez)
      │
      ▼
Para cada estrategia ENABLED y NO pausada:
  Para cada par en strategy.pairs (que cierre en este timeframe):
      │
      ├─→ decision = strategy.on_bar(pair, timeframe, bar_close_time,
      │                              market, ctx)
      │     │   (la estrategia decide internamente según SU ventana/estado;
      │     │    el Lull: B 21:00-22:59 acumula rango → NoOp;
      │     │    C 23:00-01:59 evalúa rango válido + ADX H4 + RSI + traded;
      │     │    D 02:00 server → CloseAll; viernes/rollover → NoOp)
      │     ▼
      │   Decision ∈ { Open(signal) | Close(ticket) | CloseAll(reason) | NoOp }
      │
      ├─→ NoOp ──────────────→ (opcional) LOGGEAR señal rechazada con `strategy` → siguiente par
      │
      ├─→ Open(signal):
      │     │
      │     ▼
      │   GATING DE RIESGO (motor, dos niveles — D053)
      │     ├─ Global: ¿max_open_trades cuenta? ¿correlación
      │     │          (max_same_currency_direction)? ¿drawdown global < límite?
      │     │          (drawdown global excedido = kill switch: pausa TODO)
      │     └─ Por estrategia: ¿max_open_trades de la estrategia?
      │                        ¿drawdown de la estrategia < su límite?
      │                        (excedido = pausa SOLO esa estrategia)
      │     │
      │     ▼ (gating rechaza) → LOGGEAR rechazo con `strategy` → siguiente par
      │     │ (gating acepta)
      │     ▼
      │   SIZING NOTIONAL (por estrategia):
      │     capital_asignado = balance * allocation_pct/100
      │     risk_usd = capital_asignado * percent_per_trade/100
      │     SL/TP según la señal (Lull: SL 2.5x ATR, TP = midpoint del rango)
      │     │
      │     ▼
      │   Abrir trade en MT5 con magic = base(234000) + magic_offset
      │     │
      │     ▼
      │   LOGGEAR señal aceptada + trade (con columna `strategy`)
      │     │
      │     ▼
      │   NOTIFICAR por Telegram (con la estrategia)
      │
      ├─→ Close(ticket): cerrar esa posición concreta
      │
      ├─→ CloseAll(reason): cerrar todas las posiciones de ESA estrategia
      │     (Lull lo emite a las 02:00 server como time-stop; atribuye por magic)
      │
      └─→ siguiente par / siguiente estrategia
```

## Data Flow — Monitoreo continuo

```
Cada 30 segundos (thread daemon):
      │
      ├─→ Health check MT5
      │     │
      │     └─→ ¿Desconectado? ──Sí──→ Reintentar cada 5 min → Notificar si falla
      │
      ├─→ Drawdown GLOBAL de cuenta
      │     │
      │     └─→ ¿> max_drawdown global? ──Sí──→ KILL SWITCH: pausar TODO → Notificar
      │
      ├─→ Drawdown POR estrategia (D053) — para cada estrategia:
      │     equity = capital_asignado_baseline
      │            + P&L_realizado(estrategia, DB)
      │            + P&L_flotante(estrategia, posiciones por su magic)
      │     actualizar peak en strategy_state
      │     │
      │     └─→ ¿> max_drawdown de la estrategia? ──Sí──→ pausar SOLO esa
      │           (strategy_state.paused = 1) → Notificar (evento con `strategy`)
      │
      ├─→ Detectar trades cerrados por MT5 (SL hit, TP hit), atribuir por magic
      │     └─→ Loggear (con `strategy`) → Notificar
      │
      └─→ ¿Domingo 8pm UTC-5? ──Sí──→ Generar y enviar reporte semanal
            (agregado + desglose por estrategia)

Nota: trailing stop desactivado (use_trailing_stop: false).
      Cierre por tiempo (02:00 server, GMT+2/+3) es lógica INTERNA del Lull
        (emite CloseAll vía on_bar), no de este thread ni del motor.
      Viernes no se opera (skip-Friday rule INTERNA del Lull).
      Pausa (global o por estrategia) = mantener posiciones abiertas; no se cierra
        a mercado (D053).
```

## Main Loop — Arquitectura de threads

```
Thread principal (main.py) — MOTOR genérico "reloj por suscripción" (D051):
      │
      ├─→ Calcular la UNIÓN de timeframes declarados por las estrategias enabled
      │   Loop:
      │   • Dormir (stop-aware, slices) hasta el próximo cierre de vela de la unión
      │   • MarketData: fetch dedup por (par, timeframe) para este tick
      │   • Para cada estrategia enabled & no pausada, por cada par suyo:
      │       on_bar(...) -> Decision -> gating riesgo -> ejecución/atribución
      │       -> logging (con `strategy`) -> notificación
      │   El motor NO sabe de ventanas/sesiones; eso es interno de cada estrategia
      │   (skip-Friday, time-stop 02:00, espera de rollover viven en daily_lull.py).
      │
      └─→ Thread secundario (daemon):
          Loop cada 30 segundos:
          - MT5 health + reconnect
          - Drawdown GLOBAL de cuenta (kill switch si excede)
          - Drawdown POR estrategia: P&L flotante por magic + realizado (DB),
            actualiza peak y `paused` en strategy_state
          - Detect closed trades (atribuye por magic)
          - Weekly report check

Thread Telegram (daemon):
      Corre python-telegram-bot polling en thread separado
      Recibe comandos (global y por estrategia), ejecuta acciones, envía notificaciones
```

## Component Responsibilities

| Componente | Archivo | Responsabilidad |
|---|---|---|
| MT5 Client | `drift/mt5_client.py` | Conexión, datos de mercado, estado de cuenta |
| Indicators | `drift/indicators.py` | Cálculo de RSI(14) y ATR(14) en M15, ADX(14) en H4 (lo usan las estrategias) |
| Strategy contract | `drift/strategies/base.py` | Contrato `Strategy` (`name`, `timeframes`, `on_bar() -> Decision`); tipos `Decision` (`Open`/`Close`/`CloseAll`/`NoOp`), `MarketData`, `StrategyContext`; `REGISTRY` nombre→clase (D051, D054) |
| Daily Lull | `drift/strategies/daily_lull.py` | Instancia #1: SessionState privado per par, máquina de ventana INTERNA (21:00-22:59 rango, 23:00-01:59 trading, 02:00 time-stop → CloseAll), reglas internas (skip-Friday, espera de rollover), parseo de `params` a `DailyLullParams` (D051, D055). *Reemplaza al antiguo `strategy.py`.* |
| Risk Manager | `drift/risk.py` | Riesgo en DOS niveles (D053): sizing notional por estrategia (`balance·allocation_pct·percent_per_trade`); límites globales (`max_open_trades`, `max_drawdown`=kill switch, correlación) y por estrategia (`max_open_trades`, `max_drawdown`=pausa esa) |
| Executor | `drift/executor.py` | Abrir/cerrar trades con magic = base+offset; `get_open_positions(magic)` para atribución por broker (D052) |
| Trailing Stop | `drift/trailing.py` | Monitorear y actualizar stops de trades abiertos (desactivado — `use_trailing_stop: false`) |
| Database | `drift/db.py` | SQLite CRUD: trades, señales, eventos (con columna `strategy` para atribución, D056); tabla `strategy_state` (peak/paused por estrategia) |
| Telegram | `drift/telegram_bot.py` | Notificaciones y comandos, global y por estrategia (`/strategies`, `/pause [nombre]`, `/resume [nombre]`, D057) |
| Config | `drift/config.py` | Cargar y validar config.yaml: bloque `strategies:` (mapa de instancias), `risk_global`, validación de magics únicos y allocations ≤100% (D054) |
| Motor | `main.py` | Motor genérico "reloj por suscripción": tick en la unión de timeframes, despacho a estrategias, gating de riesgo, ejecución, threads de monitoreo y Telegram (D051) |

## File Structure

```
drift/
├── main.py                  # Entry point y MOTOR genérico (reloj por suscripción)
├── config.yaml              # Configuración (no versionada con credenciales)
├── config.example.yaml      # Ejemplo de configuración (versionado, esquema strategies:)
├── requirements.txt         # Dependencias Python
├── drift/
│   ├── __init__.py
│   ├── config.py            # Carga y validación de config (strategies[], risk_global)
│   ├── mt5_client.py        # Conexión y datos de MT5 (velas por timeframe)
│   ├── indicators.py        # Cálculos técnicos (RSI, ATR, ADX)
│   ├── strategies/
│   │   ├── __init__.py      # REGISTRY {nombre: clase}
│   │   ├── base.py          # Contrato Strategy, Decision, MarketData, StrategyContext
│   │   └── daily_lull.py    # Daily Lull (SessionState + ventana interna) — reemplaza strategy.py
│   ├── risk.py              # Riesgo en dos niveles (global + por estrategia)
│   ├── executor.py          # Ejecución de trades en MT5 (magic = base+offset)
│   ├── trailing.py          # Trailing stop manager (desactivado en estrategia actual)
│   ├── db.py                # SQLite database layer (columna strategy + strategy_state)
│   └── telegram_bot.py      # Bot de Telegram (comandos global + por estrategia)
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
│   ├── engine.py                       # Adaptador genérico: recorre histórico y llama
│   │                                   #   al MISMO on_bar (única fuente de verdad, D055).
│   │                                   #   Reemplaza lull_engine.py; se abandona Backtesting.py
│   ├── run_lull.py                     # Runner del backtest Daily Lull
│   ├── optimize_lull.py                # Optimización de parámetros
│   └── results_lull_opt/              # Resultados de optimización (parámetros D029)
├── docs/
│   ├── DECISIONS.md
│   ├── ARCHITECTURE.md
│   ├── knowledge/
│   │   ├── mt5-python-api.md
│   │   ├── telegram-bot-setup.md
│   │   ├── daily-lull-scalper.md    # Estrategia activa — reglas y parámetros
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
    strategy TEXT NOT NULL DEFAULT 'daily_lull',   -- atribución legible (D056)
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
    close_reason TEXT CHECK(close_reason IN ('trailing_stop', 'take_profit', 'stop_loss', 'manual', 'drawdown_pause', 'session_close')),
    duration_minutes INTEGER,
    mt5_ticket INTEGER
);

CREATE TABLE signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL DEFAULT 'daily_lull',   -- atribución legible (D056)
    pair TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    m15_candle_time TEXT NOT NULL,
    h4_candle_time TEXT NOT NULL,
    range_high REAL,
    range_low REAL,
    range_atr_ratio REAL,
    rsi REAL,
    atr_value REAL,
    h4_adx REAL,
    decision TEXT NOT NULL CHECK(decision IN ('accepted', 'rejected')),
    reason TEXT NOT NULL,
    trade_id INTEGER REFERENCES trades(id)
);

CREATE TABLE bot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_at TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK(event_type IN ('start', 'stop', 'pause', 'resume', 'error', 'reconnect', 'drawdown_alert', 'peak_balance')),
    strategy TEXT,                                 -- NULL = evento global de cuenta;
                                                   -- no-NULL = evento de una estrategia (D056)
    detail TEXT,
    balance REAL
);

-- Drawdown por estrategia (D053, D056, D062): baseline notional persistido +
-- peak por estrategia + flag de pausa. baseline_capital NULL = aún no sembrado.
CREATE TABLE strategy_state (
    strategy         TEXT PRIMARY KEY,
    peak_equity      REAL NOT NULL,
    paused           INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL,
    baseline_capital REAL
);

CREATE INDEX idx_trades_pair ON trades(pair);
CREATE INDEX idx_trades_strategy ON trades(strategy);
CREATE INDEX idx_trades_opened ON trades(opened_at);
CREATE INDEX idx_signals_pair ON signals(pair);
CREATE INDEX idx_signals_analyzed ON signals(analyzed_at);
CREATE INDEX idx_signals_decision ON signals(decision);
CREATE INDEX idx_events_type ON bot_events(event_type);
```

### Migración (DB existente → esquema multi-estrategia, D056)

> Migración aplicada una vez sobre la DB live. Todo lo histórico es Daily Lull,
> por eso el `DEFAULT 'daily_lull'` en `trades`/`signals`. `bot_events.strategy`
> queda NULL para los eventos previos (todos de cuenta).

```sql
ALTER TABLE trades   ADD COLUMN strategy TEXT NOT NULL DEFAULT 'daily_lull';
ALTER TABLE signals  ADD COLUMN strategy TEXT NOT NULL DEFAULT 'daily_lull';
ALTER TABLE bot_events ADD COLUMN strategy TEXT;   -- NULL por defecto

CREATE TABLE IF NOT EXISTS strategy_state (
    strategy         TEXT PRIMARY KEY,
    peak_equity      REAL NOT NULL,
    paused           INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT NOT NULL,
    baseline_capital REAL
);
ALTER TABLE strategy_state ADD COLUMN baseline_capital REAL;   -- D062 (idempotente)

CREATE INDEX IF NOT EXISTS idx_trades_strategy ON trades(strategy);
```
