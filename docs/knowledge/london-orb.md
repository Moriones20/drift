# London Opening-Range Breakout — Guía de la estrategia

Referencia técnica de la estrategia #2 de Drift (`name: london_orb`). Diseño y rationale: **D067–D069** en `docs/DECISIONS.md`. Contrato e implementación bajo el framework: `docs/knowledge/strategy-framework.md` (§7 checklist, §9 lecciones). Implementación: `drift/strategies/london_orb.py` (port bajo `Strategy.on_bar`).

> 📍 **Por qué existe.** El Daily Lull (#1) falló en vivo por una sola causa: TP en el midpoint del rango ⇒ reward ≤ spread de la franja ilíquida 23:00–02:00 server (ver `daily-lull-scalper.md` y la memoria `daily-lull-live-lessons`). `london_orb` es la **inversión punto-por-punto** de ese fallo: mean reversion → momentum direccional; franja ilíquida → apertura de Londres (líquida); TP chico → TP = múltiplo del spread (R:R 1:1). El diseño nace de que **el reward debe dominar al spread**.

## Idea base

Durante la primera hora de la apertura de Londres el mercado define un rango. La tesis: cuando el precio **rompe** ese rango por cierre de vela, suele continuar en esa dirección (momentum direccional impulsado por el volumen de la apertura). Se entra en la ruptura, con SL al lado opuesto del rango y TP a 1× el ancho del rango.

Es **momentum/breakout**, no mean reversion: el trade quiere que el precio **siga**, no que vuelva. Por eso un time-stop generoso (18:00) no mata la tesis como el cierre a las 02:00 mataba la reversión del Lull (lección L3).

## Reglas paso a paso

### 1. Definir el rango (10:00–11:00 hora servidor)
- Durante la primera hora tras la apertura de Londres el bot solo observa: registra high/low de cada vela M15 (4 velas).
- A las **11:00 server** el rango se lockea con el high/low acumulados.

### 2. Validar el rango (filtro de ancho)
- El rango debe medir entre `range_atr_min` y `range_atr_max` × el ATR de la primera hora **Y** estar por encima de un **piso absoluto en pips por par** (`range_pip_floor`).
  - Por debajo del piso → el TP colapsaría al tamaño del spread, sin edge (la lección L1 codificada como gate).
  - Por encima de `range_atr_max` → la apertura ya hizo el movimiento, ruptura con poco recorrido restante.
- Si el rango no pasa el filtro, **no se opera ese día** en ese par.

### 3. Entrar al trade (ventana 11:00–18:00 server)
- **BUY**: una vela M15 **cierra por encima** del techo del rango.
- **SELL**: una vela M15 **cierra por debajo** del piso del rango.
- Se usa el *cierre* (no la mecha) para reducir rupturas falsas, consistente con D045 (evaluar velas cerradas).
- **Ambos sentidos**, sin filtro de tendencia (simplicidad; revisable en optimización — ver D067).
- **Máximo 1 trade por par por día** — si la primera ruptura falla (toca SL), no se reingresa.

### 4. Salir del trade
- **Take Profit**: `tp_mult` (=1.0) × ancho del rango, más allá del nivel de ruptura. R:R 1:1.
- **Stop Loss**: el **extremo opuesto** del rango (riesgo = ancho del rango). El SL lejos evita los stop-outs por el re-test clásico de la ruptura.
- **Time stop**: si a las **18:00 server** el trade sigue abierto, se cierra (fin de la sesión de Londres). Sin holds overnight.

### 5. Heartbeat (restricción #4 / lección L4)
- Al time-stop de las 18:00 la estrategia **siempre** notifica el cierre de sesión, incluso los días en que ningún par rompió el rango ("session closed, sin ruptura hoy"). Ningún día queda 100% mudo (análogo a D066 del Lull).

## Parámetros configurables

| Parámetro | Valor inicial | Qué controla |
|---|---|---|
| `range_start_hour` | 10 | Inicio de la ventana de definición del rango (hora server). |
| `range_end_hour` | 11 | Fin de la ventana de definición / lock del rango. |
| `range_atr_min` | (calibrar) | Ancho mínimo del rango en múltiplos de ATR. |
| `range_atr_max` | (calibrar) | Ancho máximo del rango en múltiplos de ATR. |
| `range_pip_floor` | (por par) | Piso absoluto del ancho del rango en pips — garantía reward≫spread. |
| `tp_mult` | 1.0 | TP como múltiplo del ancho del rango (R:R = `tp_mult`:1). |
| `time_stop_hour` | 18 | Hora server de cierre forzado. |
| `atr_period` | 14 | Periodo de ATR (M15) para el filtro de ancho. |

> Los valores `(calibrar)` se fijan en el Step 46 del backtest (walk-forward IS/OOS, sin overfit). No están hardcodeados en este diseño a propósito.

## Tiempos clave (hora servidor MT5)

Las horas están en **hora server MT5 (GMT+2 invierno / GMT+3 verano, anclado al cierre de NY; ver D041)**. La estrategia razona en hora server y **nunca re-localiza** (regla del contrato). Anclaje **fijo a 10:00 server** — ver D068 para el drift de DST aceptado.

| Hora servidor | UTC real (aprox.) | Local (UTC-5) | Qué pasa |
|---|---|---|---|
| 10:00 | 07:00–08:00 | 05:00 | Apertura de Londres; empieza a medir el rango |
| 11:00 | 08:00–09:00 | 06:00 | Lockea el rango; empieza a buscar rupturas |
| 18:00 | 15:00–16:00 | 13:00 | Cierra todo + heartbeat — fin de la sesión operativa |

**Importante para operación:** la ventana activa en hora local es **05:00–13:00 (UTC-5)** — muy distinta de la del Lull (13:00–18:00). Si el PC se enciende después de las 06:00 local se pierde la definición de rango y la sesión no opera ese día.

> ⚠️ **Drift de DST (D068).** ~4 semanas/año (mediados de marzo, fines de octubre) el calendario US y EU están desfasados y la apertura real de Londres cae a las 11:00 server; esas semanas el rango 10:00–11:00 captura la hora previa. Aceptado y acotado; se revisa si el backtest muestra que esas semanas rinden mal.

## Pares y por qué

Movers de Londres con buen ratio rango/spread. El backtest **rankea y poda** (como el Lull descartó USDJPY) — son candidatos, no un compromiso.

| Par | Por qué | Rango 1ª hora (aprox.) | Spread efectivo (aprox.) |
|---|---|---|---|
| GBPJPY | El mover #1 de Londres | 30–60 pips | ~2–3 pips |
| GBPUSD | Major limpio, muy líquido | 20–40 pips | ~0.5–0.8 pip |
| EURJPY | Cruce JPY con buen movimiento | 20–35 pips | ~1.0–1.5 pip |
| EURUSD | El más líquido, spread mínimo | 15–30 pips | ~0.7 pip |

Con targets de 30–50 pips el spread queda en <10% del reward: la guardia D046 prácticamente nunca rechaza (lo opuesto al Lull). Spreads a **validar con `symbol_info` del bot** antes de demo (no asumir las cifras de esta tabla).

## Riesgos conocidos

1. **Cesta correlacionada:** los 4 pares comparten GBP/EUR/JPY/USD; el guard global `max_same_currency_direction` (D053) puede bloquear entradas simultáneas en el mismo sentido. Control de riesgo correcto, no bug.
2. **NFP / noticias UK:** el primer viernes hay NFP (13:30 server) y la apertura de Londres trae data del UK; pueden pegar mid-trade. Filtro de calendario **diferido a Phase 3** (D068); el SL fijo protege.
3. **Rupturas falsas (whipsaw):** mitigadas por (a) gatillo por cierre, no mecha; (b) SL en el extremo opuesto (lejos), no en el midpoint; (c) filtro de ancho de rango.
4. **Drift de DST:** ver arriba / D068.

## Diferencias clave con el Daily Lull (qué se invirtió)

| Dimensión | Daily Lull (#1, falló) | London ORB (#2) |
|---|---|---|
| Tesis | Mean reversion | Momentum direccional (breakout) |
| Franja | 21:00–02:00 server (ilíquida) | 10:00–18:00 server (Londres, líquida) |
| TP | Midpoint del rango (6–25 pips) | 1× ancho del rango (30–50 pips) |
| R:R | < 1:1 | 1:1 |
| Reward vs spread | reward ≤ spread → sin edge en vivo | reward ≫ spread (D046 holgado) |
| Time-stop | 02:00 (corta la reversión) | 18:00 (da runway al momentum) |
| Indicadores | RSI + ATR + ADX(H4) | solo ATR |

## Lecturas relacionadas

- `docs/knowledge/strategy-framework.md` — contrato `on_bar`, §7 checklist, §9 lecciones del Lull en vivo (leer antes de implementar).
- `docs/knowledge/daily-lull-scalper.md` — la estrategia #1 (pausada); el contraste explica cada decisión de diseño de ésta.
- D067 (estrategia/mecánica), D068 (horario/DST/viernes), D069 (pivote operativo) en `docs/DECISIONS.md`.
- `backtest/engine.py` — motor de backtest unificado (mismo `on_bar`, D055); `backtest/analyze_spread_cost.py` — revalidación de costos (Step 47).
