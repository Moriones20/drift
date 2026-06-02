# Configuracion

Toda la configuracion de Drift esta en un solo archivo `config.yaml` en la raiz del proyecto. No se versiona — usar `config.example.yaml` como plantilla.

---

## broker

Credenciales para conectarse a MetaTrader 5. Los tres campos son **obligatorios**.

| Campo | Tipo | Descripcion |
|---|---|---|
| `server` | string | Nombre exacto del servidor MT5. Verificar en MT5: File > Open an Account. Para demo ICMarkets: `ICMarketsSC-Demo` |
| `login` | entero | Numero de cuenta MT5 |
| `password` | string | Password de la cuenta MT5 |

---

## strategy

Parametros de los indicadores tecnicos. Todos tienen valores por defecto y no es necesario modificarlos para el uso normal.

### Parametros de filtro

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `adx_max_threshold` | decimal | `35.0` | Umbral maximo del ADX H4 para considerar que el mercado esta en rango. Si el ADX H4 supera este valor, se bloquean las entradas (mercado con tendencia fuerte, no apto para scalper de rango). |
| `rsi_oversold` | decimal | `35.0` | Nivel de RSI por debajo del cual se considera que el precio esta sobrevendido. Las entradas de compra requieren RSI < este valor. |
| `rsi_overbought` | decimal | `65.0` | Nivel de RSI por encima del cual se considera que el precio esta sobrecomprado. Las entradas de venta requieren RSI > este valor. |

### Asian Session Scalper — ventana de sesion

La estrategia opera solo durante la sesion asiatica, cuando el mercado es mas tranquilo y los precios tienden a moverse en rango. Las horas son hora del servidor MT5 (GMT+3), no UTC.

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `session_start_hour` | entero | `21` | Hora del servidor MT5 (GMT+3) de inicio de la sesion. A las 21:00 hora servidor (= 13:00 Bogota) el bot comienza a observar el rango del precio. No se abren trades aun. |
| `range_definition_hours` | entero | `2` | Duracion en horas de la fase de definicion del rango (21:00-23:00 hora servidor MT5 = 13:00-15:00 Bogota). Durante este periodo solo se actualiza el maximo y minimo de la sesion. |
| `session_end_hour` | entero | `2` | Hora del servidor MT5 (GMT+3) de cierre de la sesion. A las 02:00 hora servidor (= 18:00 Bogota) se cierran todos los trades abiertos de la sesion y el estado se reinicia. |

### Asian Session Scalper — filtro de calidad del rango

El rango de la sesion (diferencia entre maximo y minimo de 21:00-23:00 hora servidor MT5, GMT+3) debe ser suficientemente amplio para tener margen de beneficio, pero no tan amplio que indique volatilidad excesiva.

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

---

## pairs

Lista de pares de divisas a monitorear. Debe contener al menos un par.

```yaml
pairs:
  - AUDNZD
  - EURCHF
  - EURJPY
  - GBPJPY
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
| `timezone` | string | `"UTC-5"` | Zona horaria para mostrar horas en los reportes, en los mensajes de Telegram (estado, trades) y para interpretar `weekly_report_hour`. Formatos aceptados: `UTC`, `UTC-5`, `UTC+3`. Solo afecta la presentacion — internamente todo se guarda en UTC. |
| `weekly_report_day` | string | `"sunday"` | Dia de la semana del reporte automatico (en ingles, minusculas). Con los valores por defecto (`sunday` / `20` / `UTC-5`), el disparo ocurre el domingo a las 20:00 UTC-5, que equivale al lunes a la 01:00 UTC. |
| `weekly_report_hour` | entero | `20` | Hora del reporte en la zona horaria configurada (`timezone`). Cambiar este valor mueve el disparo proporcionalmente. |

---

## system

Parametros internos del bot. No es necesario modificarlos salvo casos especificos.

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `loop_check_interval_seconds` | entero | `30` | Intervalo en segundos del hilo de monitoreo. Controla con que frecuencia se verifican trades cerrados y se chequea el drawdown. |
| `mt5_reconnect_interval_seconds` | entero | `300` | Intervalo minimo entre intentos de reconexion a MT5 si se pierde la conexion. |
| `magic_number` | entero | `234000` | Numero magico que identifica las ordenes de Drift en MT5. Permite que el bot distinga sus propias posiciones de otras que pueda haber en la cuenta. No modificar salvo que haya conflicto con otro EA. |
| `order_retry_attempts` | entero | `3` | Reintentos extra al abrir una orden ante rechazos transitorios del broker (p.ej. `retcode 10018 "market closed"` durante el rollover diario de las 00:00 hora servidor). Los rechazos fatales (stops invalidos, sin fondos) no se reintentan. `0` = un solo intento. Ver D040. |
| `order_retry_delay_seconds` | decimal | `25.0` | Segundos de espera entre reintentos de orden. El halt del rollover dura ~1-2 min; 3 intentos x 25s cubren ~75s. Cada reintento bloquea el tick M15 de ese par hasta que termina. |
