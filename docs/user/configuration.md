# Configuracion

Toda la configuracion de Drift esta en un solo archivo `config.yaml` en la raiz del proyecto. No se versiona — usar `config.example.yaml` como plantilla.

---

## broker

Credenciales para conectarse a MetaTrader 5. Los tres campos son **obligatorios**.

| Campo | Tipo | Descripcion |
|---|---|---|
| `server` | string | Nombre exacto del servidor MT5. Verificar en MT5: File > Open an Account. Para demo ICMarkets: `ICMarketsSC-MT5-Demo` |
| `login` | entero | Numero de cuenta MT5 |
| `password` | string | Password de la cuenta MT5 |

---

## strategy

Parametros de los indicadores tecnicos. Todos tienen valores por defecto y no es necesario modificarlos para el uso normal.

### Parametros generales

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `timeframe_trend` | string | `"D1"` | Timeframe para el filtro de tendencia. Solo se admiten valores validos de MT5: `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`, `W1`, `MN1`. |
| `timeframe_entry` | string | `"H4"` | Timeframe base para señales de entrada. |
| `atr_period` | entero | `14` | Periodo del ATR generico. |
| `rsi_period` | entero | `14` | Periodo del RSI generico. |
| `adx_period` | entero | `14` | Periodo del ADX generico. |
| `adx_max_threshold` | decimal | `35.0` | Umbral maximo del ADX H4 para considerar que el mercado esta en rango. Si el ADX H4 supera este valor, se bloquean las entradas (mercado con tendencia fuerte, no apto para scalper de rango). |
| `rsi_oversold` | decimal | `35.0` | Nivel de RSI por debajo del cual se considera que el precio esta sobrevendido. Las entradas de compra requieren RSI < este valor. |
| `rsi_overbought` | decimal | `65.0` | Nivel de RSI por encima del cual se considera que el precio esta sobrecomprado. Las entradas de venta requieren RSI > este valor. |

### Asian Session Scalper — ventana de sesion

La estrategia opera solo durante la sesion asiatica, cuando el mercado es mas tranquilo y los precios tienden a moverse en rango. Todo en UTC.

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `session_start_hour` | entero | `21` | Hora UTC de inicio de la sesion. A las 21:00 UTC el bot comienza a observar el rango del precio. No se abren trades aun. |
| `range_definition_hours` | entero | `2` | Duracion en horas de la fase de definicion del rango (21:00-23:00 UTC). Durante este periodo solo se actualiza el maximo y minimo de la sesion. |
| `session_end_hour` | entero | `2` | Hora UTC de cierre de la sesion. A las 02:00 UTC se cierran todos los trades abiertos de la sesion y el estado se reinicia. |

### Asian Session Scalper — filtro de calidad del rango

El rango de la sesion (diferencia entre maximo y minimo de 21:00-23:00 UTC) debe ser suficientemente amplio para tener margen de beneficio, pero no tan amplio que indique volatilidad excesiva.

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `range_atr_min` | decimal | `1.0` | Ancho minimo del rango como multiplo del ATR. Si el rango es menor a `1.0 x ATR`, el mercado es demasiado quieto y no se opera esa sesion. |
| `range_atr_max` | decimal | `4.0` | Ancho maximo del rango como multiplo del ATR. Si el rango supera `4.0 x ATR`, hay demasiada volatilidad (noticia o evento) y se salta la sesion. |

### Asian Session Scalper — parametros de riesgo e indicadores

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `sl_atr_mult` | decimal | `2.5` | Distancia del stop loss como multiplo del ATR M15. Con ATR de 0.0010 y multiplicador 2.5, el SL queda a 0.0025 del precio de entrada. |
| `m15_rsi_period` | entero | `14` | Periodo del RSI calculado sobre barras M15. Filtra entradas cuando el precio esta en zona neutral. |
| `m15_atr_period` | entero | `14` | Periodo del ATR calculado sobre barras M15. Se usa para dimensionar el rango, el SL y el TP. |
| `h4_adx_period` | entero | `14` | Periodo del ADX calculado sobre barras H4. Filtra sesiones donde hay una tendencia fuerte en curso (ADX > `adx_max_threshold`). |

---

## risk

Parametros de gestion de riesgo. Son los mas importantes para proteger el capital.

| Campo | Tipo | Default | Rango valido | Descripcion |
|---|---|---|---|---|
| `percent_per_trade` | decimal | `1.0` | 0.01 a 5.0 | Porcentaje del balance arriesgado por trade. Con 1.0%, en una cuenta de $10,000 el riesgo maximo por trade es $100. |
| `max_open_trades` | entero | `4` | 1 a 10 | Maximo de trades abiertos simultaneamente en todos los pares. |
| `max_same_currency_direction` | entero | `2` | — | Maximo de trades en la misma direccion para una misma divisa base o cotizada. Limita la exposicion correlacionada. |
| `max_drawdown_percent` | decimal | `10.0` | 1.0 a 50.0 | Si el drawdown desde el pico de balance supera este porcentaje, el bot se pausa automaticamente y notifica por Telegram. Requiere `/resume` manual para reactivar. |
| `trailing_stop_atr_multiplier` | decimal | `1.5` | — | Multiplicador del ATR para calcular la distancia del stop loss inicial. Con ATR de 0.0010 y multiplicador 1.5, el SL queda a 0.0015 del precio de entrada. |
| `take_profit_ratio` | decimal | `3.0` | — | Ratio riesgo/beneficio. Con `3.0`, el take profit se coloca a una distancia triple del stop loss (ratio 1:3). |

---

## pairs

Lista de pares de divisas a monitorear. Debe contener al menos un par.

```yaml
pairs:
  - EURUSD
  - GBPUSD
  - USDJPY
  - AUDUSD
  - USDCAD
  - EURGBP
```

Los nombres deben coincidir exactamente con los simbolos disponibles en el terminal MT5. Si un par configurado no existe o no es visible en MT5, el bot lo omite y registra un aviso en el log al iniciar.

Para agregar un par: añadirlo a la lista con el nombre exacto del simbolo en MT5.
Para quitar un par: eliminar la linea correspondiente. Los trades abiertos de ese par no se ven afectados.

---

## telegram

Credenciales del bot de Telegram. Ambos campos son **obligatorios**.

| Campo | Tipo | Descripcion |
|---|---|---|
| `bot_token` | string | Token del bot obtenido de @BotFather. Formato: `123456789:ABCdefGHIjklMNOpqrSTUvwxYZ`. |
| `chat_id` | string | ID del chat personal al que el bot enviara notificaciones y aceptara comandos. Solo este chat puede controlar el bot. |

---

## reports

Configuracion de la zona horaria y el reporte semanal automatico.

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `timezone` | string | `"UTC-5"` | Zona horaria para mostrar horas en los reportes. Solo afecta la presentacion — internamente todo se maneja en UTC. |
| `weekly_report_day` | string | `"sunday"` | Dia de la semana del reporte automatico (en ingles, minusculas). Solo referencial: el reporte se envia el lunes a la 01:00 UTC, que equivale al domingo a las 8pm UTC-5. |
| `weekly_report_hour` | entero | `20` | Hora del reporte en la zona horaria configurada. Solo referencial: ver nota arriba. |

---

## system

Parametros internos del bot. No es necesario modificarlos salvo casos especificos.

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `loop_check_interval_seconds` | entero | `30` | Intervalo en segundos del hilo de monitoreo. Controla con que frecuencia se actualizan los trailing stops, se verifican trades cerrados y se chequea el drawdown. |
| `mt5_reconnect_interval_seconds` | entero | `300` | Intervalo minimo entre intentos de reconexion a MT5 si se pierde la conexion. |
| `magic_number` | entero | `234000` | Numero magico que identifica las ordenes de Drift en MT5. Permite que el bot distinga sus propias posiciones de otras que pueda haber en la cuenta. No modificar salvo que haya conflicto con otro EA. |
| `friday_close_hour_utc` | entero | `20` | Hora UTC del viernes a partir de la cual se cierran automaticamente los trades con perdida y los trades abiertos en las ultimas 4 horas. Evita mantener posiciones durante el fin de semana con riesgo de gap. `20` equivale a las 3pm UTC-5. |
