# Drift — Roadmap

## Vision

Bot de forex autónomo que opera en MT5 con ICMarkets usando la estrategia Daily Lull Scalper (mean reversion sobre un rango en una ventana nocturna fija = el *daily lull*: en UTC corresponde al cierre de NY / apertura de Sydney, antes de la sesión asiática de Tokio; antes se llamaba "Asian Session Scalper", ver D041/D042), gestionando riesgo de forma estricta para generar retornos consistentes con mínima intervención.

> **Evolución a plataforma multi-estrategia (D050, 2026-06):** Drift está pasando de bot mono-estrategia a **plataforma que hospeda N estrategias** sobre una sola cuenta MT5, con riesgo aislado por estrategia (presupuesto notional + drawdown propio) y un brake global de cuenta. El Daily Lull es la instancia #1. El framework se construye **antes** del go-live (Phase 2.6), en paper. Ver Phase 2.6 y D050–D057.

**Audiencia:** Uso personal — trader retail con $500 de capital inicial operando desde Colombia.

**Principio rector:** Toda decisión sobre el bot se toma con datos, backtesting y análisis — nunca por emoción, una mala racha, o una buena semana. Cabeza fría siempre.

---

## Tech Stack

| Componente | Tecnología |
|---|---|
| Lenguaje | Python |
| Broker | ICMarkets (cuenta Raw Spread) |
| Plataforma | MetaTrader 5 |
| Conexión MT5 | Paquete `MetaTrader5` (PyPI) |
| Backtesting | Backtesting.py |
| Base de datos | SQLite |
| Notificaciones | Bot de Telegram |
| Configuración | config.yaml |

---

## Arquitectura

### Estrategia: Daily Lull Scalper (mean reversion sobre rango en ventana nocturna fija — el *daily lull* cierre-NY → pre-Tokio; antes "Asian Session Scalper", ver D041/D042)

Ver `docs/knowledge/daily-lull-scalper.md` para las reglas completas y `docs/DECISIONS.md` (D029) para el historial de iteraciones que llevaron a esta estrategia.

- **Ventana operativa:** 21:00-02:00 hora servidor MT5 (GMT+2/+3). Se salta el viernes completamente.
- **Definición de rango (21:00-23:00):** Se registran los high/low de cada vela M15 durante las primeras 2 horas.
- **Filtro de régimen:** ADX(14) en H4 debe estar por debajo de 35. Si el mercado va en tendencia, no se opera.
- **Señal de entrada (23:00-01:59):** BUY si precio toca el piso del rango y RSI M15 < 35. SELL si toca el techo y RSI M15 > 65.
- **Stop Loss:** 2.5x ATR(14) M15.
- **Take Profit:** Precio cruza el midpoint del rango (mean reversion al centro).
- **Time stop:** Si a las 02:00 hora servidor MT5 (GMT+2/+3) el trade sigue abierto, se cierra.
- **Sin trailing stop** (use_trailing_stop: false).
- **Position sizing:** Dinámico — 1% del balance actual por trade.

### Pares activos

AUDNZD, EURCHF, EURJPY, GBPJPY, EURGBP

USDJPY descartado: con parámetros universales da PF 0.75 (perdedor). Nota: la hipótesis original de "mercado tranquilo / sesión asiática" fue corregida — la ventana se razona en hora servidor MT5 (GMT+2/+3) y es el *daily lull* (cierre NY → pre-Tokio), no la sesión asiática real (ver D029, D037, D039, D041).

### Gestión de riesgo

- Riesgo por trade: 1% del balance actual
- Máximo trades simultáneos: 4
- Máximo 2 trades en la misma dirección de una moneda (evitar correlación)
- Máximo drawdown: 10% desde el pico del balance → pausa automática + notificación
- Position sizing dinámico: si el balance sube, los trades crecen; si baja, se reducen
- Comisiones ($7/lote round trip) incluidas en cálculos de P&L y ratios

### Fin de semana

- El viernes no se abre sesión (skip-Friday rule en el scheduler).
- No quedan trades abiertos al entrar el fin de semana por diseño (el time stop los cierra a las 02:00 hora servidor MT5 (GMT+2/+3)).

### Operación

- Horario efectivo: lunes-jueves 21:00-02:00 hora servidor MT5 (GMT+2/+3) (≈5 horas/noche)
- Ciclo: analiza los 5 pares en cada cierre de vela M15 dentro de la ventana
- Infraestructura: PC personal durante demo, VPS Windows para live
- Zonas horarias (tres capas, ver D039/D041): cálculo de sesión en hora servidor MT5 (GMT+2 invierno/+3 verano, anclado al cierre NY); almacenamiento en UTC real; presentación al usuario en UTC-5 (Bogotá)

### Notificaciones (Telegram)

**Automáticas:**
- Trade abierto (par, dirección, precio, SL, TP)
- Trade cerrado (resultado, P&L, motivo de cierre)
- Bot iniciado / apagado
- Errores
- Reporte semanal: domingos 8pm UTC-5

**Comandos:**
| Comando | Función |
|---|---|
| `/status` | Estado del bot, tiempo activo |
| `/trades` | Trades abiertos con P&L actual |
| `/history` | Últimos trades cerrados |
| `/balance` | Capital actual y rendimiento |
| `/pause` | Pausar (no abre nuevos, mantiene abiertos) |
| `/resume` | Reanudar operaciones |
| `/stop` | Cerrar todo y apagar |
| `/report` | Resumen de rendimiento bajo demanda |

### Base de datos (SQLite)

**Tabla TRADES:** id, par, dirección, precio_entrada, precio_salida, stop_loss, take_profit, tamaño_posición, ganancia_pérdida, fecha_apertura, fecha_cierre, motivo_cierre, duración.

**Tabla SEÑALES (`signals`):** id, pair, analyzed_at, m15_candle_time, h4_candle_time, range_high, range_low, range_atr_ratio, rsi, atr_value, h4_adx, decision (accepted/rejected), reason, trade_id.

**Tabla ESTADO_BOT:** id, fecha, evento, detalle, balance_actual.

### Seguridad y fallos

| Escenario | Comportamiento |
|---|---|
| Caída de internet | Stop loss en servidor MT5 protege trades abiertos |
| MT5 se cierra | Detecta desconexión, notifica, reintenta cada 5 min |
| Error del bot | Pausa automática, no abre nuevos trades, notifica |
| Drawdown > 10% | Pausa automática, notifica, requiere `/resume` manual |

### Journal dominical

Sesión de análisis post-reporte semanal:
- Revisar rendimiento de la semana
- Investigar noticias que afectaron los trades (NFP, tasas, geopolítica)
- Documentar observaciones
- NO cambiar configuración reactivamente — solo con análisis frío y evidencia

---

## Phase 1 — MVP (Demo)

Objetivo: bot funcional operando en cuenta demo de ICMarkets.

1. Scaffolding del proyecto — estructura de directorios, virtual env, dependencias (`MetaTrader5`, `python-telegram-bot`, `backtesting`, `pyyaml`, `pandas`, `pandas-ta`)
2. Módulo de configuración — cargar y validar `config.yaml`, valores por defecto, manejo de errores
3. Módulo de conexión MT5 — conectar, desconectar, verificar estado, reconexión automática
4. Módulo de datos — obtener velas M15 y H4 de MT5 para los pares configurados
5. Módulo de indicadores — calcular RSI, ATR, ADX (la formulación original con EMA/MACD es historia de iteraciones previas, deprecada por D029)
6. Backtest de validación — backtest rápido con datos históricos sobre el set de pares activos para confirmar que la estrategia tiene mérito antes de demo (las menciones a EURUSD/GBPUSD son de iteraciones previas)
7. Módulo de estrategia — lógica de mean reversion: definición de rango + filtro de régimen (ADX H4) + señal RSI sobre rango, generar señal de compra/venta/nada
8. Módulo de riesgo — position sizing dinámico (1% del balance), límite de trades (4), límite de correlación (2 por moneda), drawdown (10%), comisiones en cálculos
9. Módulo de ejecución — abrir trade en MT5, configurar SL y TP, cerrar trade
10. Módulo de trailing stop — monitorear trades abiertos, actualizar stop loss según precio
11. Base de datos SQLite — crear schema, funciones CRUD para trades, señales y estado
12. Módulo de Telegram — bot con comandos (/status, /trades, /history, /balance, /pause, /resume, /stop, /report), notificaciones automáticas
13. Loop principal — orquestador que corre cada cierre de vela M15, coordina análisis → decisión → ejecución → logging → notificación
14. Sistemas de seguridad — drawdown check, max trades, correlación, cierre de viernes, reconexión MT5, manejo de errores global
15. Reporte semanal — generación automática domingos 8pm UTC-5 vía Telegram
16. Testing en demo — verificar flujo completo: señal → trade → notificación → logging

## Phase 2 — Backtesting completo y optimización

17. Integración completa con Backtesting.py — conectar la estrategia para backtest con datos históricos
18. Descarga de datos históricos — obtener datos M15/H4 de MT5 para los 5 pares activos (USDJPY descartado, ver D029) (mínimo 2 años)
19. Backtest masivo — correr la estrategia en todos los pares, generar métricas (profit factor, drawdown, win rate, Sharpe ratio)
20. Comparación de pares — ranking de pares por rendimiento, descartar/agregar según resultados
21. Optimización de parámetros — probar variaciones de sl_atr_mult, adx_max_threshold, rsi_oversold/rsi_overbought, range_atr_min/range_atr_max (con cuidado de no overfittear)

## Phase 2.6 — Plataforma multi-estrategia (pre-live)

Objetivo: refactorizar la capa de orquestación (config, motor, riesgo, DB, Telegram, backtest) para hospedar N estrategias, con el Daily Lull como única instancia concreta (HC1). Se ejecuta **antes** de la Phase 3 (go-live) porque el Lull aún está en paper y es el momento más barato (D050). Fuente de verdad del diseño: D050–D057 y `docs/knowledge/strategy-framework.md`.

**Restricciones de la sesión de diseño (hard constraints):**
- HC1 — Solo framework; Daily Lull = única estrategia concreta.
- HC2 — Una cuenta MT5; presupuesto/límites por estrategia + brake global.
- HC3 — Timing heterogéneo: motor genérico, sin asumir la ventana del Lull.
- HC4 — El Lull está en paper/demo, no live (sin capital real sobre su lógica).

Orden de implementación (dependencias en notas de PROGRESS.md):

27. **Contrato `Strategy` + registry** (`drift/strategies/base.py`) — `Strategy` Protocol (`name`, `pairs`, `timeframes`, `on_bar`), tipos `Decision` (`Open`/`Close`/`CloseAll`/`NoOp`), `MarketData` (accessor lazy+cacheado), `StrategyContext`, registry nombre→clase. Sin dependencia de MT5. (D051)
28. **Refactor de config a `strategies[]`** — dataclasses nuevas, `risk_global`, magic base+offset, `params` opaco por estrategia, `pairs` por estrategia. Validación: magic efectivo único, suma de `allocation_pct` ≤ 100%, nombres de estrategia conocidos. Migración manual de `config.yaml` + `config.example.yaml`. (D052/D053/D054)
29. **Port del Daily Lull al contrato** (`drift/strategies/daily_lull.py`) — `DailyLullStrategy.on_bar`, `SessionState` privado, ventana/time-stop/skip-Friday/espera-rollover como lógica interna, `DailyLullParams`. **Tests de equivalencia** vs el comportamiento actual sobre el mismo histórico. (D051/D055)
30. **Migración de DB** — columna `strategy` en `trades`/`signals`, `strategy` NULL en `bot_events`, tabla `strategy_state`, CRUD actualizado, `ALTER TABLE` para la DB existente. (D056)
31. **Motor de riesgo de dos niveles** (`drift/risk.py`) — sizing notional con `allocation_pct`, límites global + por estrategia, correlación global, cómputo de equity/peak/drawdown por estrategia, brake global (kill switch). (D053)
32. **Motor genérico** (reescritura del loop de `main.py`) — reloj por suscripción (unión de timeframes), `MarketData` con fetch dedup por (par,timeframe) por tick, despacho a estrategias enabled/no-pausadas, gating de riesgo, ejecución, atribución por magic, logging con `strategy`. (D051)
33. **Thread de monitoreo multi-estrategia** — P&L flotante por magic, peak/drawdown por estrategia → `strategy_state`, pausa por estrategia, detección de cierres atribuida. (D053/D056)
34. **Telegram multi-estrategia** — desglose por estrategia en `/status`, `/balance`, `/trades`, `/report`; `/pause [estrategia]`, `/resume [estrategia]`, nuevo `/strategies`. Reporte semanal con sección por estrategia. (D057)
35. **Unificación del backtest** (`backtest/engine.py`) — adaptador propio que recorre el histórico y consume el **mismo** `on_bar`; reemplaza `backtest/lull_engine.py`; se abandona Backtesting.py. Tests de equivalencia. Portfolio backtest **diferido** (ver Futuro). (D055)
36. **Validación end-to-end en paper** — correr el framework con el Lull como única estrategia; verificar paridad con el comportamiento pre-refactor; luego proceder a Phase 3 (go-live).

## Phase 2.7 — Hardening del modelo de riesgo (pre-live)

Objetivo: corregir los defectos de corrección del modelo de riesgo por estrategia detectados en la auditoría 2026-06-09 (post-Phase 2.6), antes del go-live. El hallazgo raíz: la equity por estrategia **cuenta el P&L realizado dos veces** (el baseline se deriva del balance vivo, que ya incluye el realized, y luego se vuelve a sumar), lo que descalibra el freno de drawdown por estrategia (~2× demasiado sensible al 100%) y, peor, **contamina entre estrategias** en multi-estrategia — socavando el aislamiento de riesgo que justifica D053. Decisiones: D062 (baseline persistido), D063 (reset de peak en `/resume`), D064 (pausa bloquea solo aperturas). Fuente de la auditoría: nota de PROGRESS.md "Auditoría 2026-06-09".

Orden de implementación (37 primero — todos dependen del helper canónico; 38/39 tras 37; 40/41/42 independientes):

37. **Equity canónica + baseline persistido** (`drift/risk.py`, `drift/db.py`, `drift/engine.py`, `main.py`) — añadir columna `baseline_capital` a `strategy_state` (migración idempotente); UNA función canónica de equity/drawdown por estrategia que reemplaza las tres implementaciones divergentes (engine, monitor, telegram); sembrado del baseline una sola vez como `balance_seed × alloc/100 − realized_lifetime_seed` (resuelve el doble conteo histórico) y reset del peak inflado existente en esa primera computación; fix de `get_stats(strategy=)` para filtrar también el peak por estrategia (#5). Tests. (D062 — arregla #1, #5)
38. **`/resume` resetea el peak por estrategia** (`drift/db.py`, `drift/telegram_bot.py`) — nueva `reset_strategy_peak` (escritura directa, no `MAX`); `/resume <estrategia>` resetea `peak_equity = equity_actual` y limpia la pausa → ventana de drawdown nueva, el monitor ya no re-pausa a los 30s. `/resume` global sigue como hoy (reset de peak global **diferido** — su storage event-based necesita su propio diseño, ver Futuro). Tests. (D063 — arregla #2, #3)
39. **Telegram usa la equity canónica** (`drift/telegram_bot.py`) — `/balance` y `/strategies` calculan el drawdown por estrategia con el helper canónico de Step 37 y floating real (fetch de posiciones por magic; costo trivial en comando manual), en vez de la fórmula incoherente actual (peak inflado vs balance crudo). Tests. (#4)
40. **Pausa bloquea solo aperturas** (`drift/engine.py`) — las estrategias pausadas **siguen agendadas** (hoy se excluyen del scheduler); el bloqueo se mueve al manejo de decisiones: `open` se descarta bajo pausa (global o por estrategia), `close`/`close_all` se honran siempre (el time-stop de 02:00 cierra aunque la cuenta esté en kill switch). Log de pausa una vez por tick, no por par (#9). Tests. (D064 — arregla #6, #9)
41. **Limpieza** — mover `_pip_multiplier`/`_pip_value` de `main.py` a un módulo compartido y reapuntar el import del engine (#11); borrar `closed_bars` de `engine.py` (solo lo usan tests → reapuntar a `EngineMarketData`) (#10); borrar `check_all_risk` y `StrategyConfig` (sin uso); la notificación "SESSION CLOSED" solo se envía si se cerró ≥1 posición (#7). Tests/ruff. (#7, #10, #11)
42. **Reintentos de orden fuera del lock** (`drift/engine.py`) — sacar los reintentos de `open_trade` (~75s con sleeps) de dentro de `_trade_lock` para no bloquear el thread de monitoreo (detección de cierres / drawdown) ese tiempo: sizing/gating bajo lock → soltar → ejecución+retries → re-lock para registrar. Concurrencia delicada, test propio. (#8)

## Phase 2.8 — Estrategia #2: London Opening-Range Breakout (`london_orb`)

Objetivo: implementar la **primera estrategia nueva sobre el framework** (la #2), diseñada para invertir cada modo de falla del Daily Lull (D067–D069). Es la primera prueba real de que la abstracción `Strategy.on_bar` aguanta una estrategia con timing/lógica distintos (HC3). El Lull se pausa/desactiva y pivota todo el capital notional a `london_orb` (D069). Fuente de verdad del diseño: **D067–D069**, `docs/knowledge/london-orb.md`, y `docs/knowledge/strategy-framework.md` §7 (checklist) / §9 (lecciones).

**Tesis:** momentum direccional en la apertura de Londres — define el rango de la primera hora (10:00–11:00 server) y opera la ruptura por cierre M15, R:R 1:1, flat a las 18:00. Líquido, target grande vs spread, sin franja ilíquida ni rollover. Ver D067.

**Restricciones de diseño (no negociables, salen del post-mortem del Lull — §9):**
- R:R ≥ 1:1 que sobreviva al spread+slippage **reales** del par/hora, no al fill sin costo del backtest.
- Backtest que modele costos de ejecución; revalidar el edge con spread inflado antes de demo.
- Time-stop que respete la duración del trade ganador (18:00 da 5–7h de runway).
- Heartbeat en todo camino silencioso (el cierre de las 18:00 siempre notifica, incl. días sin ruptura).
- Riesgo aislado: magic_offset único (1), allocation ≤ 100%, drawdown propio.

Orden de implementación (43–44 secuenciales; 45 tras 44; 46 puede arrancar tras 44; 47 tras 46; 48/49 independientes; 50 al final):

43. **`LondonOrbParams` + parsing** (`drift/strategies/london_orb.py`) — dataclass de params con defaults y parseo del dict `params` crudo (`range_start_hour`, `range_end_hour`, `range_atr_min/max`, `range_pip_floor` por par, `tp_mult`, `time_stop_hour`, `atr_period`). Opaco para el motor (D054/D067).
44. **`LondonOrbStrategy.on_bar`** (`drift/strategies/london_orb.py`) — estado per-par (rango high/low, locked, traded), define-range 10:00–11:00, lock + filtro de ancho (ATR × `[min,max]` + piso en pips), gatillo por cierre M15 en ambos sentidos, `Open(signal)` con SL=extremo opuesto / TP=1× ancho, `CloseAll` al time-stop 18:00 con **heartbeat** (notifica incl. sin ruptura), 1 trade/par/día. **Sin tocar MT5/DB/reloj** (todo por `market`/`ctx`). Registrar en el registry. (D051/D067/D068)
44b. **Heartbeat de cierre de sesión** — verificar que el motor emite la notificación de cierre a las 18:00 también sin posiciones (análogo a D066 del Lull); ajustar si el wording/condición del engine es específico del Lull. (Restricción #4)
45. **Unit tests de `on_bar`** (`tests/test_london_orb_strategy.py`) — señales aceptadas y rechazadas con su `reason`/`rejection_reason`: ruptura long/short, rango fuera de `[min,max]`, rango bajo el piso de pips, ruptura dentro de la ventana de definición (ignorada), 2ª señal del día (ignorada), time-stop cierra, heartbeat sin ruptura. (strategy-framework §7.7)
46. **Datos + backtest unificado** — descargar M15 ≥2a de GBPJPY/GBPUSD/EURJPY/EURUSD; correr `backtest/engine.py` (mismo `on_bar`, D055); **rankear y podar pares**; **calibrar** `range_atr_min/max` y `range_pip_floor` por par (sin overfit — walk-forward IS/OOS como `validate_oos.py`). PF/Sharpe/DD sobre la curva de equity.
47. **Revalidación de costos de ejecución** — modelar el spread de la hora de Londres (inflado) sobre los fills del backtest (extender `analyze_spread_cost.py`) y confirmar que el edge sobrevive con R:R ≥ 1:1 **antes** de demo. Restricción #2 / lección L2. Si el reward realista ~ spread, no hay edge → volver a 46.
48. **Config** — añadir el bloque `london_orb` en `config.yaml` y `config.example.yaml` (magic_offset 1, allocation 100, risk estándar, params calibrados); `daily_lull` → `enabled: false`; validar (magic único, suma allocations ≤100%). (D069)
49. **Docs de usuario** — `configuration.md` (bloque + params de `london_orb`); `commands.md` (confirmar que `/pause london_orb` · `/resume london_orb` · `/strategies` funcionan genéricos, D057); `getting-started.md` si cambia la ventana operativa visible (ORB: 02:00–10:00 UTC-5 verano — madrugada — no la tarde del Lull).
50. **Validación end-to-end en paper** — desplegar en demo; verificar el ciclo: wake 10:00 → define-range → lock 11:00 (filtro ATR/pips) → ruptura → fill con D046 holgado → time-stop 18:00 + heartbeat → reset. Acumular trades antes de Phase 3 (go-live).

**Paralelismo:** Steps 43→44→45 son secuenciales (mismo archivo + tests). Step 46 (datos+backtest) puede arrancar en cuanto exista `on_bar` (44) y corre en paralelo con 45. Steps 48 y 49 son independientes una vez calibrados los params (47).

## Phase 3 — Live

22. Migrar a VPS Windows
23. Configurar cuenta live ICMarkets
24. Ajustar config.yaml con credenciales live
25. Monitoreo intensivo primera semana (logs, trades, latencia)
26. Operación autónoma con journal dominical

## Futuro — Exploración

- **Estrategia híbrida:** filtro de régimen de mercado (tendencia vs rango) para aplicar trend following o mean reversion según corresponda
- **Filtro de noticias:** integrar calendario económico para evitar operar durante eventos de alto impacto (si el journal muestra que es necesario)
- **Más pares:** expandir pool de pares basado en datos de demo/live
- **Dashboard web:** interfaz visual para monitoreo (opcional, Telegram puede ser suficiente)
- ~~**Segunda estrategia concreta:** diseñar e implementar la estrategia #2 sobre el framework — primera prueba real de que la abstracción aguanta timing heterogéneo (HC3)~~ → realizada como **Phase 2.8** (London ORB, D067; sesión `/spec` 2026-06-19). Trend-following 24/5 en H1 queda como candidata #3.
- **NY Opening-Range Breakout:** misma mecánica que `london_orb` en la sesión de NY (13:00–17:00 server); reservada como variante futura si el ORB de Londres valida (D067)
- **Portfolio backtest:** simular varias estrategias sobre una misma curva de equity (cap global de trades + correlación + drawdown de cuenta compartidos); diferido hasta que exista la estrategia #2 (D055)
- ~~**Multi-estrategia:** correr varias estrategias en paralelo con capital asignado~~ → promovido a Phase 2.6 (D050)

---

## Non-goals

- No es un producto para vender — es uso personal
- No usar ML puro como estrategia (lección aprendida: overfitting)
- No timeframes ultracortos (M1, M5); el scalping de la estrategia opera en M15, no por debajo
- No grid trading
- No operar durante noticias de alto impacto sin análisis previo
- No cambiar configuración reactivamente por una mala semana
