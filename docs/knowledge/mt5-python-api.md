# MT5 Python API — Guía de integración

## Instalación

```bash
pip install MetaTrader5
```

Requiere MT5 terminal instalado y corriendo en Windows. No funciona en Linux/Mac.

## Conexión

```python
import MetaTrader5 as mt5

# Inicializar conexión
if not mt5.initialize():
    print(f"Error: {mt5.last_error()}")
    mt5.shutdown()

# Conectar a cuenta específica
authorized = mt5.login(
    login=12345678,
    password="password",
    server="ICMarketsSC-MT5"  # Servidor de ICMarkets (Seychelles)
)

if not authorized:
    print(f"Login failed: {mt5.last_error()}")
```

## Obtener datos de mercado

```python
import pandas as pd
from datetime import datetime

# Obtener velas
rates = mt5.copy_rates_from_pos(
    "EURUSD",           # símbolo
    mt5.TIMEFRAME_H4,   # timeframe
    0,                   # posición inicial (0 = más reciente)
    100                  # cantidad de velas
)

# Convertir a DataFrame
df = pd.DataFrame(rates)
df['time'] = pd.to_datetime(df['time'], unit='s')

# Timeframes disponibles
# mt5.TIMEFRAME_M1, M5, M15, M30
# mt5.TIMEFRAME_H1, H4
# mt5.TIMEFRAME_D1, W1, MN1
```

## Información de cuenta

```python
account_info = mt5.account_info()
balance = account_info.balance
equity = account_info.equity
margin_free = account_info.margin_free
```

## Enviar órdenes

```python
# Orden de compra
request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": "EURUSD",
    "volume": 0.01,  # lot size (micro lot)
    "type": mt5.ORDER_TYPE_BUY,
    "price": mt5.symbol_info_tick("EURUSD").ask,
    "sl": 1.0800,    # stop loss
    "tp": 1.1000,    # take profit
    "deviation": 20,  # max slippage en puntos
    "magic": 234000,  # ID del bot
    "comment": "drift_buy",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_IOC,
}

result = mt5.order_send(request)

if result.retcode != mt5.TRADE_RETCODE_DONE:
    print(f"Order failed: {result.comment}")
else:
    print(f"Order placed, ticket: {result.order}")
```

## Cerrar posición

```python
# Obtener posiciones abiertas
positions = mt5.positions_get(symbol="EURUSD")

# Cerrar una posición específica
position = positions[0]
close_request = {
    "action": mt5.TRADE_ACTION_DEAL,
    "symbol": position.symbol,
    "volume": position.volume,
    "type": mt5.ORDER_TYPE_SELL if position.type == 0 else mt5.ORDER_TYPE_BUY,
    "position": position.ticket,
    "price": mt5.symbol_info_tick(position.symbol).bid if position.type == 0 else mt5.symbol_info_tick(position.symbol).ask,
    "deviation": 20,
    "magic": 234000,
    "comment": "drift_close",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_IOC,
}

result = mt5.order_send(close_request)
```

## Modificar SL/TP (para trailing stop)

```python
request = {
    "action": mt5.TRADE_ACTION_SLTP,
    "symbol": "EURUSD",
    "position": ticket_number,
    "sl": new_stop_loss,
    "tp": existing_take_profit,
}

result = mt5.order_send(request)
```

## Obtener posiciones abiertas

```python
# Todas las posiciones
positions = mt5.positions_get()

# Por símbolo
positions = mt5.positions_get(symbol="EURUSD")

# Por magic number (identificador del bot)
positions = mt5.positions_get(group="*")
# Filtrar por magic:
my_positions = [p for p in positions if p.magic == 234000]
```

## Info del símbolo

```python
symbol_info = mt5.symbol_info("EURUSD")
point = symbol_info.point          # mínima variación de precio
digits = symbol_info.digits        # decimales
tick_value = symbol_info.trade_tick_value  # valor de un tick en moneda de cuenta
contract_size = symbol_info.trade_contract_size  # tamaño del contrato (100,000 para forex)
```

## Calcular tamaño de posición (con comisiones)

```python
def calculate_lot_size(balance, risk_pct, sl_points, symbol, commission_per_lot=7.0):
    """
    balance: balance actual de la cuenta
    risk_pct: porcentaje de riesgo (0.01 = 1%)
    sl_points: distancia del stop loss en puntos
    symbol: par de forex
    commission_per_lot: comisión round trip por lote estándar ($7 ICMarkets Raw)
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

    # Redondear al step mínimo del broker
    lot_step = info.volume_step
    lot_size = round(lot_size / lot_step) * lot_step

    # Respetar mínimos y máximos
    lot_size = max(info.volume_min, min(lot_size, info.volume_max))
    return lot_size
```

## Desconexión

```python
mt5.shutdown()
```

## Notas importantes

- El terminal MT5 debe estar corriendo en background para que la API funcione
- El `magic` number (234000 en los ejemplos) identifica las órdenes del bot vs órdenes manuales
- `type_filling` puede variar por broker. ICMarkets generalmente acepta `IOC` o `FOK`
- Los precios de SL/TP deben estar en precio absoluto, no en pips
- Para demo: usar el servidor "ICMarketsSC-Demo" o similar
- Verificar el nombre exacto del servidor en MT5 → File → Open an Account
