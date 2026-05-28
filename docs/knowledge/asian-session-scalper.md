# Asian Session Scalper — Guía de la estrategia

Referencia técnica de la estrategia activa del bot Drift. Para el código que la implementa en backtest, ver `backtest/asian_engine.py`. Para el plan de migración al bot live, ver `docs/plans/asian-session-scalper-live.md`.

## Idea base

El mercado forex tiene tres sesiones principales: **Asian (Tokyo)**, **London** y **New York**. Durante las horas previas y al inicio de la sesión asiática (21:00-02:00 GMT) el mercado está muy tranquilo — Londres ya cerró, New York está cerrando y Tokyo aún no opera con fuerza. En esa calma los precios se mueven dentro de un rango estrecho y tienden a volver al centro (mean reversion).

La estrategia identifica el rango de la noche y, cuando el precio toca un extremo con confirmación de sobreventa/sobrecompra, apuesta a que regresará al medio.

## Reglas paso a paso

### 1. Definir el rango (21:00-23:00 GMT)
- Durante las primeras 2 horas el bot solo observa: registra el high y low de cada vela M15
- A las 23:00 el rango se "lockea" con el high y low acumulados

### 2. Validar el rango
- **Filtro de ancho**: el rango debe medir entre `1.0x` y `3.0x` el ATR(14) en M15
  - Menor a 1x ATR → demasiado estrecho, sin espacio para mover el TP
  - Mayor a 3x ATR → noche volátil, hipótesis rota, no es seguro entrar

### 3. Filtro de régimen — ADX H4
- El ADX(14) calculado en velas H4 debe estar **por debajo de `25`**
- ADX mide la fuerza de la tendencia: si está alto, el mercado va en tendencia y la mean reversion no funciona
- El ADX se calcula sobre el H4 anterior (shifted) para evitar look-ahead bias

### 4. Entrar al trade (ventana 23:00-01:59 GMT)
- **BUY**: precio toca o rompe el piso del rango **Y** RSI(14) en M15 < `30` (sobreventa confirma extremo)
- **SELL**: precio toca o rompe el techo del rango **Y** RSI(14) en M15 > `70` (sobrecompra confirma)
- **Máximo 1 trade por sesión por par** — si la primera señal falla, no se insiste

### 5. Salir del trade
- **Take Profit**: precio cruza el **medio del rango** (la media donde tiende a regresar)
- **Stop Loss**: `1.5x ATR(14)` por debajo del entry (BUY) o por encima (SELL)
- **Time stop**: si a las **02:00 GMT** el trade sigue abierto, se cierra — la ventana segura termina y comienza la volatilidad pre-London

## Parámetros configurables

| Parámetro | Valor por defecto | Qué controla |
|---|---|---|
| `sl_atr_mult` | 1.5 | Multiplicador de ATR para el stop loss. Más alto = SL más lejos = menos stops pero pérdidas mayores |
| `adx_max_threshold` | 25.0 | Máximo ADX H4 permitido. Más alto = más permisivo, más trades pero en mercados más volátiles |
| `rsi_oversold` | 30.0 | RSI para señal de compra. Más alto (ej: 35) = más señales pero menos confirmación |
| `rsi_overbought` | 70.0 | RSI para señal de venta. Más bajo (ej: 65) = más señales pero menos confirmación |
| `range_atr_min` | 1.0 | Ancho mínimo del rango en múltiplos de ATR |
| `range_atr_max` | 3.0 | Ancho máximo del rango |

Estos valores son los baseline del primer backtest. La optimización fina por par se documenta en `docs/plans/asian-session-scalper-live.md`.

## Tiempos clave (fijos, no configurables)

| Hora GMT | Qué pasa |
|---|---|
| 21:00 | Se abre nueva sesión, empieza a medir el rango |
| 23:00 | Se lockea el rango, se empiezan a buscar entries |
| 02:00 | Se cierra todo — fin de la ventana segura |

Todos los horarios se manejan internamente en UTC. La presentación al usuario (Telegram) usa UTC-5.

## Pares y por qué

Los pares ideales son los que **no tienen actividad fuerte durante la sesión asiática temprana**:

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
| USDJPY | Tokyo opera USDJPY activamente desde las 23:00 GMT — rompe la hipótesis de mercado tranquilo. En el primer backtest dio PF 0.89. |

## Métricas del primer backtest (datos reales MT5, 2 años de M15)

| Par | Return | Win Rate | PF | Max DD | Trades | Sharpe |
|---|---|---|---|---|---|---|
| EURCHF | +5.84% | 78.3% | **8.17** | -0.26% | 46 | 3.16 |
| AUDNZD | +5.60% | 66.2% | 4.29 | -0.30% | 77 | 3.91 |
| EURJPY | +2.55% | 71.4% | 3.58 | -0.42% | 42 | 1.80 |
| GBPJPY | +1.47% | 61.9% | 2.02 | -0.44% | 42 | 1.22 |
| EURGBP | +1.31% | 65.8% | 2.63 | -0.39% | 38 | 1.50 |
| USDJPY | -0.18% | 37.5% | 0.89 | -0.74% | 32 | DROP |

Portfolio total: PF 3.60 promedio, peor drawdown -0.7%, 277 trades en 2 años.

## Riesgos conocidos

1. **Holidays japoneses** (Golden Week en mayo, Año Nuevo): los spreads explotan y la liquidez desaparece. Sin filtro de calendario implementado todavía.
2. **Anuncios del BOJ** (Banco de Japón): pueden ocurrir durante la ventana y mover los JPY crosses violentamente.
3. **NFP americano** (viernes 12:30 GMT): no cae en la ventana, pero el gap del viernes de la semana siguiente sí. El cierre automático del viernes a las 20:00 UTC protege contra esto.
4. **Cambio DST**: el horario GMT no cambia, pero los pares contra USD/EUR sí experimentan cambios de actividad alrededor del cambio de hora. Sin ajuste necesario porque comparamos contra UTC, no contra hora local.

## Lecturas relacionadas

- Estrategias verificadas similares: Night Hunter Pro, Evening Scalper Pro, GerFX Density Scalper
- Convención de "no usar martingala/grid" — esas técnicas inflan resultados a corto plazo pero explotan en noticias
- `docs/knowledge/mt5-python-api.md` — cómo se obtienen los datos M15 desde MT5
- `backtest/asian_engine.py` — implementación de referencia (fuente de verdad de la lógica)
