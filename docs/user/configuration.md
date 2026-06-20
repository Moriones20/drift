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

## risk_global

> ⚠️ **D072 — todo el riesgo es ahora POR ESTRATEGIA.** Solo `max_same_currency_direction` (correlacion) se sigue enforzando a nivel de cuenta. `max_open_trades` y `max_drawdown_percent` quedan en el archivo por compatibilidad pero **NO se usan** (no hay cap global de trades ni kill switch global). Cada estrategia se frena sola con sus propios limites (ver el bloque `risk` por estrategia).

| Campo | Tipo | Default | Estado | Descripcion |
|---|---|---|---|---|
| `max_open_trades` | entero | `8` | **UNUSED (D072)** | Antes era el cap global de trades. Removido: cada estrategia usa su propio `max_open_trades`. |
| `max_drawdown_percent` | decimal | `10.0` | **UNUSED (D072)** | Antes era el kill switch global. Removido: cada estrategia tiene su propio brake de drawdown. |
| `max_same_currency_direction` | entero | `2` | **activo** | Maximo de trades en la misma direccion para una misma divisa base o cotizada, **a nivel de cuenta** sin importar que estrategia los abrio. Unico freno global que queda — la correlacion es exposicion real de la cuenta unica (ver D053/D072). |

---

## strategies

> ⚠️ Esquema multi-estrategia (en implementacion — ver D050-D057)

Drift es una **plataforma que hospeda N estrategias** sobre una sola cuenta MT5 (ver D050). El antiguo bloque plano `strategy:` (singular) se reemplaza por un **mapa `strategies:`** donde cada clave es el nombre de una instancia de estrategia. Hoy hay una sola estrategia, `daily_lull`, pero el esquema esta preparado para alojar mas.

Cada estrategia se aisla del resto: tiene su propio presupuesto de capital, sus propios pares, su propio riesgo y sus propios parametros. El motor solo conoce los campos compartidos (`enabled`, `magic_offset`, `allocation_pct`, `pairs`, `risk`); el bloque `params` es **opaco** para el motor — cada estrategia parsea sus propios parametros (ver D054).

### Campos comunes de cada estrategia

| Campo | Tipo | Descripcion |
|---|---|---|
| `enabled` | booleano | Activa o desactiva la estrategia sin borrar su configuracion. `false` la apaga (util para A/B o para detenerla via config + reinicio). |
| `magic_offset` | entero | Desplazamiento entero **unico** por estrategia. El magic efectivo en MT5 = `system.magic_number` (base) + `magic_offset` (ver D052). El Daily Lull usa `magic_offset: 0` → magic efectivo **234000** (identico al actual, asi adopta sus posiciones demo vivas sin huerfanos). Dos estrategias no pueden compartir el mismo magic efectivo: el bot rechaza la config al arrancar. |
| `allocation_pct` | decimal | Asignacion **notional** de capital para esta estrategia, en porcentaje (ver bloque siguiente). La **suma de todas las `allocation_pct` debe ser ≤ 100** (ver D053). |
| `pairs` | lista | Pares de divisas que opera **esta** estrategia. `pairs` ya no es global: baja a cada estrategia. La union de los pares de todas las estrategias activas es lo que el bot activa en el Market Watch de MT5. Los nombres deben coincidir exactamente con los simbolos del terminal MT5; si un par no existe o no es visible, el bot lo omite y registra un aviso al iniciar. |
| `risk` | mapa | Limites de riesgo **propios** de la estrategia (ver abajo). |
| `params` | mapa | Parametros internos de la estrategia, opacos para el motor (ver abajo). |

### Asignacion notional (`allocation_pct`)

La asignacion de capital es **notional**: no reserva ni mueve dinero real entre estrategias (hay una sola cuenta). Solo cambia el numero sobre el que cada estrategia dimensiona sus posiciones. El sizing de cada trade se calcula asi (ver D053):

```
capital_asignado(estrategia) = balance_cuenta * (allocation_pct / 100)
risk_usd(trade)              = capital_asignado * (percent_per_trade / 100)
```

Ejemplo: con un balance de $10,000, una estrategia con `allocation_pct: 60` y `percent_per_trade: 1.0` arriesga `10000 * 0.60 * 0.01 = $60` por trade, en lugar de $100. Bajar la asignacion **encoge** el sizing de esa estrategia; no aparta capital fisico.

**Regla:** la suma de `allocation_pct` de todas las estrategias debe ser **≤ 100%**. Se permite menos de 100% (deja un colchon sin asignar), pero el bot **rechaza** una suma mayor a 100% al arrancar, porque implicaria apalancamiento accidental (contra el principio "cabeza fria").

### Bloque `risk` por estrategia

| Campo | Tipo | Default | Rango valido | Descripcion |
|---|---|---|---|---|
| `percent_per_trade` | decimal | `1.0` | 0.01 a 5.0 | Porcentaje del **capital asignado** a la estrategia arriesgado por trade (ver formula de sizing arriba). Este parametro paso de global a por-estrategia. |
| `max_open_trades` | entero | `4` | 1 a 10 | Maximo de trades abiertos simultaneamente **de esta estrategia**. (Ya no hay cap global, D072 — cada estrategia se limita sola.) |
| `max_drawdown_percent` | decimal | `10.0` | 1.0 a 50.0 | **Kill switch por estrategia:** si el drawdown de la curva de equity propia de la estrategia (desde su peak) supera este porcentaje, se pausa **solo esa** estrategia (mantiene sus posiciones, deja de abrir nuevas) hasta `/resume`. No afecta a las demas. |
| `max_daily_loss_pct` | decimal | `5.0` | 0 a 50 | **Tope de perdida diaria** (% del capital asignado). Si el P&L del dia de la estrategia (realizado + flotante) cae a `-este%`, deja de abrir por el resto del **dia**. `0` lo desactiva. (D072) |
| `max_daily_profit_pct` | decimal | `6.0` | 0 a 50 | **Tope de ganancia diaria.** Si el P&L del dia sube a `+este%`, deja de abrir por el resto del dia (asegura la ganancia). `0` lo desactiva. |
| `max_weekly_loss_pct` | decimal | `10.0` | 0 a 50 | Igual que el diario pero sobre la **semana** (resetea domingo 00:00 server). `0` lo desactiva. |
| `max_weekly_profit_pct` | decimal | `12.0` | 0 a 50 | Tope de ganancia **semanal**. `0` lo desactiva. |

> Los topes diario/semanal son un **gate que se recalcula en cada intento de apertura** (no una pausa persistente): se **auto-resetean** al cambiar de dia/semana sin necesitar `/resume`. El dia empieza a las 00:00 hora server; la semana, el domingo 00:00 server (ver D072).

### Bloque `params` por estrategia (daily_lull)

El bloque `params` es opaco para el motor: contiene los parametros propios de la logica de cada estrategia. Para `daily_lull` son los antiguos parametros de indicadores, ventana de sesion, filtro de rango e indicadores.

**Filtro de entrada**

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `adx_max_threshold` | decimal | `35.0` | Umbral maximo del ADX H4 para considerar que el mercado esta en rango. Si el ADX H4 supera este valor, se bloquean las entradas (mercado con tendencia fuerte, no apto para scalper de rango). |
| `rsi_oversold` | decimal | `35.0` | Nivel de RSI por debajo del cual se considera que el precio esta sobrevendido. Las entradas de compra requieren RSI < este valor. |
| `rsi_overbought` | decimal | `65.0` | Nivel de RSI por encima del cual se considera que el precio esta sobrecomprado. Las entradas de venta requieren RSI > este valor. |

**Ventana de sesion** — la estrategia opera solo durante el *daily lull* (cierre de Nueva York, antes de la apertura de Tokio), cuando el mercado es mas tranquilo y los precios tienden a moverse en rango. Las horas son hora del servidor MT5 (GMT+2 invierno / GMT+3 verano), no UTC.

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `session_start_hour` | entero | `21` | Hora del servidor MT5 (GMT+2/+3) de inicio de la sesion. A las 21:00 hora servidor (= 13:00 Bogota) el bot comienza a observar el rango del precio. No se abren trades aun. |
| `range_definition_hours` | entero | `2` | Duracion en horas de la fase de definicion del rango (21:00-23:00 hora servidor MT5 = 13:00-15:00 Bogota). Durante este periodo solo se actualiza el maximo y minimo de la sesion. |
| `session_end_hour` | entero | `2` | Hora del servidor MT5 (GMT+2/+3) de cierre de la sesion. A las 02:00 hora servidor (= 18:00 Bogota) se cierran todos los trades abiertos de la sesion y el estado se reinicia. |

**Filtro de calidad del rango** — el rango de la sesion (diferencia entre maximo y minimo de 21:00-23:00 hora servidor MT5) debe ser suficientemente amplio para tener margen de beneficio, pero no tanto que indique volatilidad excesiva.

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `range_atr_min` | decimal | `1.0` | Ancho minimo del rango como multiplo del ATR. Si el rango es menor a `1.0 x ATR`, el mercado es demasiado quieto y no se opera esa sesion. |
| `range_atr_max` | decimal | `4.0` | Ancho maximo del rango como multiplo del ATR. Si el rango supera `4.0 x ATR`, hay demasiada volatilidad (noticia o evento) y se salta la sesion. |

**Riesgo de la entrada e indicadores**

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `sl_atr_mult` | decimal | `2.5` | Distancia del stop loss como multiplo del ATR M15. Con ATR de 0.0010 y multiplicador 2.5, el SL queda a 0.0025 del precio de entrada. |
| `m15_rsi_period` | entero | `14` | Periodo del RSI calculado sobre barras M15. Filtra entradas cuando el precio esta en zona neutral. |
| `m15_atr_period` | entero | `14` | Periodo del ATR calculado sobre barras M15. Se usa para dimensionar el rango, el SL y el TP. |
| `h4_adx_period` | entero | `14` | Periodo del ADX calculado sobre barras H4. Filtra sesiones donde hay una tendencia fuerte en curso (ADX > `adx_max_threshold`). |

### Bloque `params` por estrategia (london_orb)

> ⚠️ Estrategia **#2, activa** (D067-D069). El `daily_lull` (#1) quedo `enabled: false` tras su post-mortem en vivo; `london_orb` toma el 100% del capital. Guia completa: `docs/knowledge/london-orb.md`. Valores de ancho de rango **pendientes de calibracion** (backtest, Phase 2.8 Step 46).

`london_orb` opera la **ruptura del rango de la apertura de Londres**: define el rango de la primera hora (10:00-11:00 hora servidor), entra cuando una vela M15 **cierra** mas alla del extremo, con SL en el extremo opuesto y TP a 1x el ancho del rango (R:R 1:1), flat a las 18:00. Solo usa ATR. Horas en hora servidor MT5 (anclaje fijo 10:00, ver D068).

| Campo | Tipo | Default | Descripcion |
|---|---|---|---|
| `range_start_hour` | entero | `10` | Hora servidor de inicio de la ventana de definicion del rango (= 02:00 Bogota, verano). |
| `range_end_hour` | entero | `11` | Hora servidor de lock del rango; desde aqui se buscan rupturas. |
| `time_stop_hour` | entero | `18` | Hora servidor de cierre forzado de todos los trades (= 10:00 Bogota, verano). Sin holds overnight. |
| `range_atr_min` | decimal | *(calibrar)* | Ancho minimo del rango como multiplo del ATR. |
| `range_atr_max` | decimal | *(calibrar)* | Ancho maximo del rango como multiplo del ATR. |
| `range_pip_floor` | mapa par→pips | *(calibrar)* | Piso absoluto del ancho del rango en pips, por par. Garantia estructural de que el TP nunca colapse al tamano del spread. |
| `tp_mult` | decimal | `1.0` | TP como multiplo del ancho del rango (R:R = `tp_mult`:1). |
| `atr_period` | entero | `14` | Periodo del ATR M15 para el filtro de ancho. |

**Ventana operativa en hora local (UTC-5):** **02:00-10:00 Bogota (verano; +1h invierno)** — la madrugada, distinta de la del Daily Lull (13:00-18:00, tarde). Si el PC se enciende despues de las 03:00 local, se pierde la definicion de rango y no se opera ese dia. Operar de madrugada en maquina de casa refuerza la necesidad de VPS (leccion L5).

### Ejemplo completo

Tras el pivote (D069): `daily_lull` desactivado, `london_orb` activo al 100% (offset 1). Las estrategias desactivadas no cuentan en la suma de allocations:

```yaml
risk_global:
  max_open_trades: 4
  max_drawdown_percent: 10.0
  max_same_currency_direction: 2

strategies:
  daily_lull:                  # #1 — desactivado tras el post-mortem (D069); config preservada
    enabled: false
    magic_offset: 0            # magic efectivo = 234000 (base + offset)
    allocation_pct: 100        # ignorado mientras enabled:false (no cuenta en la suma)
    pairs: [AUDNZD, EURCHF, EURJPY, GBPJPY, EURGBP]
    risk:
      percent_per_trade: 1.0
      max_open_trades: 4
      max_drawdown_percent: 10.0
    params:
      rsi_oversold: 35.0
      rsi_overbought: 65.0
      adx_max_threshold: 35.0
      session_start_hour: 21
      range_definition_hours: 2
      session_end_hour: 2
      range_atr_min: 1.0
      range_atr_max: 4.0
      sl_atr_mult: 2.5
      m15_rsi_period: 14
      m15_atr_period: 14
      h4_adx_period: 14

  london_orb:                  # #2 — activo (D067-D069)
    enabled: true
    magic_offset: 1            # magic efectivo = 234001 (unico vs daily_lull)
    allocation_pct: 100        # suma de ENABLED debe ser <= 100
    pairs: [GBPJPY, GBPUSD, EURJPY, EURUSD]
    risk:
      percent_per_trade: 1.0
      max_open_trades: 4       # debe ser <= risk_global.max_open_trades
      max_drawdown_percent: 10.0
    params:
      range_start_hour: 10
      range_end_hour: 11
      time_stop_hour: 18
      range_atr_min: 0.5       # placeholder — calibrar (Step 46)
      range_atr_max: 2.0       # placeholder — calibrar
      range_pip_floor:         # placeholder — calibrar por par
        GBPJPY: 18
        GBPUSD: 10
        EURJPY: 12
        EURUSD: 8
      tp_mult: 1.0
      atr_period: 14
```

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
| `magic_number` | entero | `234000` | Magic number **base** de Drift en MT5. El magic efectivo de cada estrategia = esta base + el `magic_offset` de la estrategia (ver D052). Permite atribuir cada posicion del broker a la estrategia que la abrio y distinguir las ordenes de Drift de cualquier otra en la cuenta. No modificar salvo que haya conflicto con otro EA. |
| `order_retry_attempts` | entero | `3` | Reintentos extra al abrir una orden ante rechazos transitorios del broker (p.ej. `retcode 10018 "market closed"` durante el rollover diario de las 00:00 hora servidor). Los rechazos fatales (stops invalidos, sin fondos) no se reintentan. `0` = un solo intento. Ver D040. |
| `order_retry_delay_seconds` | decimal | `25.0` | Segundos de espera entre reintentos de orden. El halt del rollover dura ~1-2 min; 3 intentos x 25s cubren ~75s. Cada reintento bloquea el tick M15 de ese par hasta que termina. |
| `rollover_settle_seconds` | entero | `150` | Segundos que espera el bot antes de evaluar/ejecutar **solo** la vela que cierra a las 00:00 hora servidor (el rollover diario del broker). Deja que el mercado reabra y la estrategia re-evalua sobre precio fresco, evitando llenar en el gap de reapertura. Las demas velas se ejecutan a los ~5s normales. Ver D044. |
| `min_reward_fraction` | decimal | `0.5` | Fraccion minima del objetivo (`\|tp - entry\|`) que debe sobrevivir al spread vivo para abrir la entrada. Una orden a mercado llena al ask (compra) / bid (venta); si el spread es ancho (p.ej. post-rollover) el fill puede quedar pasado el TP, convirtiendo un "take profit" en perdida. Con `0.5` se descarta la entrada si el spread se come mas de la mitad del objetivo. `0.0` desactiva la guardia. Ver D046. |
