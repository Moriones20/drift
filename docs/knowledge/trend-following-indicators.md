> ⚠️ ESTRATEGIA HISTÓRICA — ya no activa. El bot usa Daily Lull Scalper (ver D029 y `docs/knowledge/daily-lull-scalper.md`). Este documento se conserva como referencia para una posible estrategia híbrida futura (D023).

# Trend Following Indicators — Guía técnica

## Estrategia de Drift

Drift usa un sistema de trend following multi-timeframe:
- **D1 (diario):** Filtro de dirección con EMA 50/200
- **H4 (4 horas):** Señal de entrada con MACD (12,26,9)
- **ATR (14):** Gestión de riesgo (stop loss y position sizing)

---

## EMA — Exponential Moving Average

### Qué es
Promedio ponderado del precio que da más peso a los datos recientes. A diferencia de la SMA (Simple Moving Average), reacciona más rápido a cambios de precio.

### Fórmula
```
multiplier = 2 / (period + 1)
EMA_hoy = (precio_cierre - EMA_ayer) * multiplier + EMA_ayer
```

### Uso en Drift (D1)

**EMA 50 (rápida):** Refleja la tendencia de ~2.5 meses.
**EMA 200 (lenta):** Refleja la tendencia de ~10 meses.

**Señales:**
- EMA 50 cruza POR ENCIMA de EMA 200 → tendencia ALCISTA (Golden Cross) → solo buscamos compras
- EMA 50 cruza POR DEBAJO de EMA 200 → tendencia BAJISTA (Death Cross) → solo buscamos ventas
- EMA 50 ≈ EMA 200 (sin separación clara) → SIN TENDENCIA → no operar

**Implementación con pandas-ta:**
```python
import pandas_ta as ta

df['ema_50'] = ta.ema(df['close'], length=50)
df['ema_200'] = ta.ema(df['close'], length=200)

# Determinar tendencia
if df['ema_50'].iloc[-1] > df['ema_200'].iloc[-1]:
    trend = 'bullish'
elif df['ema_50'].iloc[-1] < df['ema_200'].iloc[-1]:
    trend = 'bearish'
else:
    trend = 'none'
```

### Nota importante
Se necesitan al menos 200 velas para que la EMA 200 sea significativa. Al pedir datos de MT5, solicitar mínimo 250 velas D1.

---

## MACD — Moving Average Convergence Divergence

### Qué es
Indicador que muestra la relación entre dos EMAs del precio. Tiene 3 componentes:
- **MACD line:** EMA(12) - EMA(26) del precio
- **Signal line:** EMA(9) de la MACD line
- **Histograma:** MACD line - Signal line

### Uso en Drift (H4)

El histograma es la señal clave:
- Histograma cruza de **negativo a positivo** + tendencia D1 alcista → **COMPRAR**
- Histograma cruza de **positivo a negativo** + tendencia D1 bajista → **VENDER**
- Cualquier otra combinación → **NO OPERAR**

**Implementación:**
```python
macd = ta.macd(df['close'], fast=12, slow=26, signal=9)
df['macd'] = macd.iloc[:, 0]          # MACD line
df['macd_signal'] = macd.iloc[:, 1]   # Signal line
df['macd_hist'] = macd.iloc[:, 2]     # Histograma

# Detectar cruce del histograma
prev_hist = df['macd_hist'].iloc[-2]
curr_hist = df['macd_hist'].iloc[-1]

if prev_hist < 0 and curr_hist > 0:
    signal = 'buy_signal'
elif prev_hist > 0 and curr_hist < 0:
    signal = 'sell_signal'
else:
    signal = 'no_signal'
```

### Filtro de calidad
Para evitar señales débiles, considerar que el cruce sea significativo:
- `abs(curr_hist) > threshold` donde threshold puede ser un % del ATR

---

## ATR — Average True Range

### Qué es
Mide la volatilidad promedio del precio. No indica dirección, solo cuánto se mueve el precio en promedio.

### Fórmula
```
True Range = max(
    high - low,
    abs(high - previous_close),
    abs(low - previous_close)
)
ATR = SMA(True Range, 14)  # o EMA, según implementación
```

### Uso en Drift

**Stop Loss = 1.5x ATR(14) del H4**
```
Si ATR = 0.0050 (50 pips):
  SL = 1.5 * 0.0050 = 0.0075 (75 pips)
  Para BUY: SL = precio_entrada - 0.0075
  Para SELL: SL = precio_entrada + 0.0075
```

**Take Profit = 2x Stop Loss (ratio 1:2)**
```
  TP = 2 * 0.0075 = 0.0150 (150 pips)
  Para BUY: TP = precio_entrada + 0.0150
  Para SELL: TP = precio_entrada - 0.0150
```

**Implementación:**
```python
df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)

atr_value = df['atr'].iloc[-1]
sl_distance = atr_value * 1.5
tp_distance = sl_distance * 2.0

if direction == 'buy':
    stop_loss = entry_price - sl_distance
    take_profit = entry_price + tp_distance
elif direction == 'sell':
    stop_loss = entry_price + sl_distance
    take_profit = entry_price - tp_distance
```

---

## Trailing Stop — Lógica

El trailing stop sube (en compras) o baja (en ventas) conforme el precio avanza a favor:

```python
def calculate_trailing_stop(direction, current_price, current_sl, atr_value):
    trail_distance = atr_value * 1.5

    if direction == 'buy':
        new_sl = current_price - trail_distance
        return max(new_sl, current_sl)  # solo sube, nunca baja
    elif direction == 'sell':
        new_sl = current_price + trail_distance
        return min(new_sl, current_sl)  # solo baja, nunca sube
```

---

## Flujo completo de decisión

```
1. Obtener velas D1 (250+)
2. Calcular EMA 50 y EMA 200
3. ¿EMA 50 > EMA 200? → tendencia alcista → solo buscar compras
   ¿EMA 50 < EMA 200? → tendencia bajista → solo buscar ventas
   ¿Igual? → no operar

4. Obtener velas H4 (100+)
5. Calcular MACD (12, 26, 9)
6. ¿Histograma cruzó en la dirección correcta? → señal válida
   ¿No? → loggear y esperar

7. Calcular ATR (14) del H4
8. Calcular SL (1.5x ATR), TP (2x SL)
9. Calcular position size (1% del balance / SL en moneda, incluyendo comisión)
10. Verificar límites (max 4 trades, max 2 misma dirección por moneda, drawdown < 10%)
11. Ejecutar trade
```

---

## Position Sizing con comisiones

ICMarkets cobra $7 por lote estándar (100,000 unidades) round trip. La comisión se escala proporcionalmente al tamaño del lote.

```python
def calculate_lot_size(balance, risk_pct, sl_points, symbol, commission_per_lot=7.0):
    """
    Calcula el tamaño de posición incluyendo comisiones.

    balance: balance actual
    risk_pct: riesgo como decimal (0.01 = 1%)
    sl_points: distancia del SL en puntos
    symbol: par de forex
    commission_per_lot: comisión round trip por lote estándar ($7 ICMarkets)
    """
    info = mt5.symbol_info(symbol)
    risk_amount = balance * risk_pct
    tick_value = info.trade_tick_value

    # Primer cálculo sin comisión
    raw_lot_size = risk_amount / (sl_points * tick_value)

    # Restar comisión proporcional del riesgo disponible
    commission = raw_lot_size * commission_per_lot
    adjusted_risk = risk_amount - commission
    lot_size = adjusted_risk / (sl_points * tick_value)

    # Redondear al step mínimo
    lot_step = info.volume_step
    lot_size = round(lot_size / lot_step) * lot_step
    lot_size = max(info.volume_min, min(lot_size, info.volume_max))

    return lot_size
```

---

## Correlación de monedas — lógica de conteo

Cada par tiene dos monedas. Un trade afecta ambas:
- EURUSD BUY = long EUR + short USD
- GBPUSD BUY = long GBP + short USD
- USDJPY BUY = long USD + short JPY

Para verificar el límite de 2 trades en la misma dirección de una moneda:

```python
def get_currency_exposure(open_trades):
    """
    Retorna un dict con la exposición neta por moneda.
    Ejemplo: {'EUR': 1, 'USD': -2, 'GBP': 1}
    Positivo = long, Negativo = short.
    """
    exposure = {}
    for trade in open_trades:
        base = trade.pair[:3]   # EURUSD → EUR
        quote = trade.pair[3:]  # EURUSD → USD

        if trade.direction == 'buy':
            exposure[base] = exposure.get(base, 0) + 1
            exposure[quote] = exposure.get(quote, 0) - 1
        else:  # sell
            exposure[base] = exposure.get(base, 0) - 1
            exposure[quote] = exposure.get(quote, 0) + 1

    return exposure

def can_open_trade(pair, direction, open_trades, max_per_currency=2):
    """
    Verifica si abrir este trade excedería el límite de correlación.
    """
    exposure = get_currency_exposure(open_trades)
    base = pair[:3]
    quote = pair[3:]

    if direction == 'buy':
        new_base = exposure.get(base, 0) + 1
        new_quote = exposure.get(quote, 0) - 1
    else:
        new_base = exposure.get(base, 0) - 1
        new_quote = exposure.get(quote, 0) + 1

    # Verificar que ninguna moneda exceda el límite en una dirección
    if abs(new_base) > max_per_currency or abs(new_quote) > max_per_currency:
        return False
    return True
```

Ejemplo:
- Trade 1: EURUSD BUY → EUR=+1, USD=-1 ✅
- Trade 2: GBPUSD BUY → GBP=+1, USD=-2 ✅ (USD=-2, justo en el límite)
- Trade 3: AUDUSD BUY → AUD=+1, USD=-3 ❌ (USD excede -2, rechazado)
- Trade 3 alternativa: EURJPY BUY → EUR=+2, JPY=-1 ✅

---

## Cierre de viernes — lógica

El mercado forex cierra el viernes ~22:00 UTC. Drift cierra trades en pérdida a las **20:00 UTC (3pm UTC-5)** para evitar gaps de fin de semana.

```python
def friday_close_check(current_utc_time, open_trades):
    """
    Si es viernes después de las 20:00 UTC, cerrar trades en pérdida
    o recién abiertos (< 4 horas). Mantener los que están en ganancia.
    """
    if current_utc_time.weekday() != 4:  # 4 = Friday
        return []

    if current_utc_time.hour < 20:
        return []

    trades_to_close = []
    for trade in open_trades:
        if trade.current_profit <= 0:
            trades_to_close.append(trade)

    return trades_to_close
```

Un trade "en ganancia" significa que el P&L actual (precio actual vs precio de entrada, incluyendo comisión) es positivo.

---

## Detección de cierre de vela H4

Las velas H4 cierran a horas fijas UTC: 00:00, 04:00, 08:00, 12:00, 16:00, 20:00.

El bot NO hace polling constante esperando el cierre. En su lugar, calcula cuándo es el próximo cierre y programa un sleep:

```python
import time
from datetime import datetime, timezone, timedelta

def seconds_until_next_h4_close():
    """Calcula segundos hasta el próximo cierre de vela H4."""
    now = datetime.now(timezone.utc)
    current_hour = now.hour
    # Próxima hora que sea múltiplo de 4
    next_h4 = (current_hour // 4 + 1) * 4
    next_close = now.replace(hour=next_h4 % 24, minute=0, second=5, microsecond=0)
    if next_h4 >= 24:
        next_close += timedelta(days=1)
        next_close = next_close.replace(hour=0, minute=0, second=5)
    return (next_close - now).total_seconds()

# Main loop
while running:
    # 1. Esperar al próximo cierre de H4
    wait_seconds = seconds_until_next_h4_close()
    time.sleep(wait_seconds)

    # 2. Analizar los 6 pares
    for pair in config.pairs:
        analyze_and_maybe_trade(pair)

    # Entre cierres de H4: monitoreo continuo cada 30 segundos
    # (trailing stops, drawdown, health check)
    # Esto corre en un thread separado
```

El `+5 segundos` es para asegurar que la vela ya cerró en MT5 y los datos están disponibles.

---

## Criterios de validación del backtest rápido (Step 6)

El backtest de validación en EURUSD y GBPUSD (2+ años de datos) debe cumplir TODOS estos criterios para continuar a demo:

| Métrica | Mínimo aceptable |
|---|---|
| Profit Factor | > 1.2 (por cada $1 perdido, gana $1.20) |
| Win Rate | > 35% (trend following gana poco pero grande) |
| Max Drawdown | < 25% (en backtest, no en live) |
| Total trades | > 30 (suficientes para ser estadísticamente relevante) |
| Expectancy | > 0 (ganancia promedio por trade positiva) |

**Si NO pasa:** No significa que la estrategia es basura — puede necesitar ajuste de parámetros (ATR multiplier, TP ratio). Documentar resultados en DECISIONS.md y ajustar con justificación.

**Si pasa:** Continuar a demo con confianza de que la estrategia tiene fundamento estadístico.

**Expectancy formula:**
```
Expectancy = (Win% × Avg_Win) - (Loss% × Avg_Loss)
```
Debe ser positivo. Ejemplo: 40% win rate, avg win $10, avg loss $5:
```
(0.40 × 10) - (0.60 × 5) = 4.0 - 3.0 = $1.0 por trade
```

## Librería recomendada

`pandas-ta` sobre `ta-lib`:
- `ta-lib` requiere compilación C (problemático en Windows)
- `pandas-ta` es puro Python, se instala con pip sin problemas
- Mismos resultados, menos fricción de setup

```bash
pip install pandas-ta
```
