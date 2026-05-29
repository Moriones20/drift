# Drift — Roadmap

## Vision

Bot de forex autónomo que opera en MT5 con ICMarkets usando la estrategia Asian Session Scalper (mean reversion durante la sesión asiática), gestionando riesgo de forma estricta para generar retornos consistentes con mínima intervención.

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

### Estrategia: Asian Session Scalper (mean reversion durante sesión asiática)

Ver `docs/knowledge/asian-session-scalper.md` para las reglas completas y `docs/DECISIONS.md` (D029) para el historial de iteraciones que llevaron a esta estrategia.

- **Ventana operativa:** 21:00-02:00 GMT. Se salta el viernes completamente.
- **Definición de rango (21:00-23:00):** Se registran los high/low de cada vela M15 durante las primeras 2 horas.
- **Filtro de régimen:** ADX(14) en H4 debe estar por debajo de 35. Si el mercado va en tendencia, no se opera.
- **Señal de entrada (23:00-01:59):** BUY si precio toca el piso del rango y RSI M15 < 35. SELL si toca el techo y RSI M15 > 65.
- **Stop Loss:** 2.5x ATR(14) M15.
- **Take Profit:** Precio cruza el midpoint del rango (mean reversion al centro).
- **Time stop:** Si a las 02:00 GMT el trade sigue abierto, se cierra.
- **Sin trailing stop** (use_trailing_stop: false).
- **Position sizing:** Dinámico — 1% del balance actual por trade.

### Pares activos

AUDNZD, EURCHF, EURJPY, GBPJPY, EURGBP

USDJPY descartado: Tokyo opera activamente USDJPY desde las 23:00 GMT, rompiendo la hipótesis de mercado tranquilo. Con parámetros universales da PF 0.75 (perdedor). Ver D029.

### Gestión de riesgo

- Riesgo por trade: 1% del balance actual
- Máximo trades simultáneos: 4
- Máximo 2 trades en la misma dirección de una moneda (evitar correlación)
- Máximo drawdown: 10% desde el pico del balance → pausa automática + notificación
- Position sizing dinámico: si el balance sube, los trades crecen; si baja, se reducen
- Comisiones ($7/lote round trip) incluidas en cálculos de P&L y ratios

### Fin de semana

- El viernes no se abre sesión asiática (skip-Friday rule en el scheduler).
- No quedan trades abiertos al entrar el fin de semana por diseño (el time stop los cierra a las 02:00 GMT).

### Operación

- Horario efectivo: lunes-jueves 21:00-02:00 GMT (≈5 horas/noche)
- Ciclo: analiza los 5 pares en cada cierre de vela M15 dentro de la ventana
- Infraestructura: PC personal durante demo, VPS Windows para live
- Zona horaria interna: UTC. Presentación al usuario: UTC-5 (Colombia)

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

**Tabla SEÑALES:** id, par, fecha, vela_h4, ema_50, ema_200, dirección_tendencia, macd_valor, macd_señal, macd_histograma, atr_valor, decisión (aceptada/rechazada), motivo, trade_id.

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
4. Módulo de datos — obtener velas D1 y H4 de MT5 para los pares configurados
5. Módulo de indicadores — calcular EMA 50, EMA 200, MACD (12,26,9), ATR(14)
6. Backtest de validación — backtest rápido en EURUSD y GBPUSD con datos históricos para confirmar que la estrategia tiene mérito antes de demo
7. Módulo de estrategia — lógica de trend following: filtro D1 + señal H4, generar señal de compra/venta/nada
8. Módulo de riesgo — position sizing dinámico (1% del balance), límite de trades (4), límite de correlación (2 por moneda), drawdown (10%), comisiones en cálculos
9. Módulo de ejecución — abrir trade en MT5, configurar SL y TP, cerrar trade
10. Módulo de trailing stop — monitorear trades abiertos, actualizar stop loss según precio
11. Base de datos SQLite — crear schema, funciones CRUD para trades, señales y estado
12. Módulo de Telegram — bot con comandos (/status, /trades, /history, /balance, /pause, /resume, /stop, /report), notificaciones automáticas
13. Loop principal — orquestador que corre cada cierre de vela H4, coordina análisis → decisión → ejecución → logging → notificación
14. Sistemas de seguridad — drawdown check, max trades, correlación, cierre de viernes, reconexión MT5, manejo de errores global
15. Reporte semanal — generación automática domingos 8pm UTC-5 vía Telegram
16. Testing en demo — verificar flujo completo: señal → trade → notificación → logging

## Phase 2 — Backtesting completo y optimización

17. Integración completa con Backtesting.py — conectar la estrategia para backtest con datos históricos
18. Descarga de datos históricos — obtener datos D1/H4 de MT5 para los 6 pares (mínimo 2 años)
19. Backtest masivo — correr la estrategia en todos los pares, generar métricas (profit factor, drawdown, win rate, Sharpe ratio)
20. Comparación de pares — ranking de pares por rendimiento, descartar/agregar según resultados
21. Optimización de parámetros — probar variaciones de ATR multiplier, TP ratio, EMA periods (con cuidado de no overfittear)

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
- **Multi-estrategia:** correr varias estrategias en paralelo con capital asignado

---

## Non-goals

- No es un producto para vender — es uso personal
- No usar ML puro como estrategia (lección aprendida: overfitting)
- No scalping ni timeframes bajos (M1, M5, M15)
- No grid trading
- No operar durante noticias de alto impacto sin análisis previo
- No cambiar configuración reactivamente por una mala semana
