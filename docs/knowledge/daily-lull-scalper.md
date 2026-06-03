# Daily Lull Scalper — Guía de la estrategia

Referencia técnica de la estrategia activa del bot Drift. Para el código que la implementa en backtest, ver `backtest/lull_engine.py`. Para el rationale de los parámetros universales, ver D029 en `docs/DECISIONS.md`.

## Idea base

La estrategia opera una ventana horaria fija definida en **hora del servidor MT5** (ICMarkets = GMT+2 invierno / GMT+3 verano, anclado al cierre de NY; ver D041): **21:00-02:00 hora servidor**. Esta franja es el *daily lull* — el período de menor liquidez del día forex, entre el cierre de Nueva York y la apertura de Tokio. Identifica el rango de las primeras dos horas y, cuando el precio toca un extremo con confirmación de sobreventa/sobrecompra, apuesta a que regresará al medio (mean reversion).

> 📍 **Qué es esta ventana realmente (corroborado 2026-06-02, ver D041).** El nombre original era "Asian Session Scalper", pero era geográficamente incorrecto: la hora servidor 21:00-02:00 equivale en **UTC real** a **18:00-23:00 (verano) / 19:00-00:00 (invierno)** — el *daily lull* entre el cierre de Nueva York y la apertura de Tokio (la ventana **termina** cuando Tokio abre a las 00:00 UTC). De hecho captura la apertura de Sídney, aún más tranquila que Tokio. El edge es real y **sensible a la hora** (mover la ventana ±1h reduce el PF fuertemente), lo que confirma que explota un límite de régimen intradía específico, no la "calma asiática" del nombre viejo. Renombrada a *Daily Lull Scalper* (ver D042). ✅ Validación out-of-sample re-confirmada (2026-06-03, `backtest/validate_oos.py`): walk-forward con params optimizados solo en IS → **5/5 pares rentables en OOS no visto, retención media de PF 1.12**. El edge generaliza (ver D043).

## Reglas paso a paso

### 1. Definir el rango (21:00-23:00 hora servidor)
- Durante las primeras 2 horas el bot solo observa: registra el high y low de cada vela M15
- A las 23:00 (hora servidor) el rango se "lockea" con el high y low acumulados

### 2. Validar el rango
- **Filtro de ancho**: el rango debe medir entre `1.0x` y `4.0x` el ATR(14) en M15
  - Menor a 1x ATR → demasiado estrecho, sin espacio para mover el TP
  - Mayor a 4x ATR → noche volátil, hipótesis rota, no es seguro entrar

### 3. Filtro de régimen — ADX H4
- El ADX(14) calculado en velas H4 debe estar **por debajo de `35`**
- ADX mide la fuerza de la tendencia: si está alto, el mercado va en tendencia y la mean reversion no funciona
- El ADX se calcula sobre el H4 anterior (shifted) para evitar look-ahead bias

### 4. Entrar al trade (ventana 23:00-01:59 hora servidor)
- **BUY**: precio toca o rompe el piso del rango **Y** RSI(14) en M15 < `35` (sobreventa confirma extremo)
- **SELL**: precio toca o rompe el techo del rango **Y** RSI(14) en M15 > `65` (sobrecompra confirma)
- **Máximo 1 trade por sesión por par** — si la primera señal falla, no se insiste

### 5. Salir del trade
- **Take Profit**: precio cruza el **medio del rango** (la media donde tiende a regresar)
- **Stop Loss**: `2.5x ATR(14)` por debajo del entry (BUY) o por encima (SELL)
- **Time stop**: si a las **02:00 (hora servidor)** el trade sigue abierto, se cierra — fin de la ventana operativa

## Parámetros configurables

| Parámetro | Valor (universal optimizado) | Qué controla |
|---|---|---|
| `sl_atr_mult` | 2.5 | Multiplicador de ATR para el stop loss. Más alto = SL más lejos = menos stops pero pérdidas mayores |
| `adx_max_threshold` | 35.0 | Máximo ADX H4 permitido. Más alto = más permisivo, más trades pero en mercados más volátiles |
| `rsi_oversold` | 35.0 | RSI para señal de compra. Más alto = más señales pero menos confirmación |
| `rsi_overbought` | 65.0 | RSI para señal de venta. Más bajo = más señales pero menos confirmación |
| `range_atr_min` | 1.0 | Ancho mínimo del rango en múltiplos de ATR |
| `range_atr_max` | 4.0 | Ancho máximo del rango |

Estos son los valores universales tras la optimización con grid search de 1620 combinaciones × 6 pares + búsqueda universal. Detalle en D029 (`docs/DECISIONS.md`).

## Tiempos clave (fijos, no configurables)

Las horas de la estrategia están en **hora del servidor MT5 (GMT+2 invierno / GMT+3 verano, anclado al cierre de NY; ver D041)**. El scheduler de `main.py` deriva el offset del servidor al arrancar y lo re-deriva al inicio de cada sesión (`get_server_utc_offset`), alineándose a hora servidor para que coincida con los timestamps de las velas (ver D037).

| Hora servidor | UTC real | Local (UTC-5) | Qué pasa |
|---|---|---|---|
| 21:00 | 18:00 | 13:00 | Se abre nueva sesión, empieza a medir el rango |
| 23:00 | 20:00 | 15:00 | Se lockea el rango, se empiezan a buscar entries |
| 02:00 | 23:00 | 18:00 | Se cierra todo — fin de la ventana operativa |

**Importante para operación:** la ventana activa del bot en hora local es **13:00-18:00 (UTC-5)**. Si el PC se enciende después de las 13:00 local, se pierde parte o toda la definición de rango y la sesión no opera. La presentación al usuario (Telegram) usa UTC-5.

## Pares y por qué

Los pares ideales son los que **se mueven en rango (sin tendencia fuerte) durante la ventana operativa** (18:00-23:00 UTC):

| Par | Comportamiento esperado |
|---|---|
| EURCHF | Europa duerme, par muy estable, rangos limpios |
| EURGBP | Igual, baja volatilidad nocturna |
| AUDNZD | Australia/NZ abren pero la relación AUD/NZD se mantiene estable |
| GBPJPY | JPY cross con rango definido antes de la apertura completa de Tokyo |
| EURJPY | Similar a GBPJPY |

**Pares problemáticos:**

| Par | Por qué falla |
|---|---|
| USDJPY | Descartado: comportamiento perdedor en la ventana (PF 0.89 en el primer backtest, PF 0.75 con parámetros universales). Ver D029. |

## Métricas (datos reales MT5, snapshot 2026-06-03, parámetros universales de `config.yaml`)

> Reproducibles con `python backtest/run_lull.py`. Las cifras previas de D029 (EURCHF "PF 8.17", 277 trades) venían de un snapshot no reproducible con parámetros distintos — reconciliado en D043.

| Par | Return | Win Rate | PF | Max DD | Trades | Sharpe |
|---|---|---|---|---|---|---|
| AUDNZD¹ | +16.15% | 77.8% | 5.83 | -0.26% | 198 | 7.32 |
| EURCHF | +12.65% | 78.0% | 4.14 | -0.74% | 141 | 4.18 |
| GBPJPY | +5.63% | 67.9% | 2.68 | -0.66% | 137 | 2.96 |
| EURGBP | +4.89% | 69.1% | 2.77 | -0.46% | 139 | 3.00 |
| EURJPY | +4.43% | 61.5% | 2.07 | -0.73% | 148 | 1.91 |

¹ AUDNZD: el broker (ICMarkets demo) solo tiene M15 desde **2025-01-02** (~1.4 años); el resto cubre 2 años completos (2024-06-03 → 2026-06-03). Límite del broker, no corregible.

Portfolio (5 pares vivos × $500 = $2500): **+8.75%** ($+218.77), PF medio ~3.0, peor drawdown -0.74%, 763 trades.

USDJPY descartado: actividad direccional fuerte en la ventana (PF 0.75, -1.82%, WR 42.9%). Ver D029/D043.

## Riesgos conocidos

1. **Holidays japoneses** (Golden Week en mayo, Año Nuevo): los spreads explotan y la liquidez desaparece. Sin filtro de calendario implementado todavía.
2. **Anuncios del BOJ** (Banco de Japón): pueden ocurrir durante la ventana y mover los JPY crosses violentamente.
3. **NFP americano** (viernes 12:30 UTC): no cae en la ventana operativa (que en UTC real es 18:00-23:00).
4. **Cambio DST**: el servidor de ICMarkets **sí aplica DST** (GMT+2 invierno / GMT+3 verano, anclado al cierre de NY; ver D041 — la afirmación previa de "sin DST" era incorrecta). Como el reloj del servidor está anclado al cierre de NY, "21:00 servidor" es siempre el mismo momento de mercado todo el año, así que la ventana sigue al *daily lull* sin necesidad de tocar la config. El scheduler re-deriva el offset al inicio de cada sesión para captar la transición DST sin reiniciar; aun así, conviene reiniciar el bot tras cada cambio de DST de EE.UU. como respaldo (ver D041, D042).

## Lecturas relacionadas

- Estrategias verificadas similares: Night Hunter Pro, Evening Scalper Pro, GerFX Density Scalper
- Convención de "no usar martingala/grid" — esas técnicas inflan resultados a corto plazo pero explotan en noticias
- `docs/knowledge/mt5-python-api.md` — cómo se obtienen los datos M15 desde MT5
- `backtest/lull_engine.py` — implementación de referencia (fuente de verdad de la lógica)
