# Strategy Framework — Cómo añadir una estrategia a Drift

Referencia de implementación del framework multi-estrategia de Drift. Su propósito es que una sesión futura pueda **añadir una estrategia nueva sin re-investigar el diseño**. La fuente de verdad son las decisiones **D050–D057** en `docs/DECISIONS.md`; este doc las explica de forma operativa. Si algo aquí contradice esas decisiones, gana la decisión.

> 📍 **Contexto (2026-06-08, sesión `/spec`).** Drift pivotó de bot mono-estrategia a **plataforma que hospeda N estrategias sobre una sola cuenta MT5** (D050). El Daily Lull es la **instancia de referencia** (#1). El framework se construye en paper, antes del go-live, y el Lull sale live ya sobre esta arquitectura. Todo lo que sigue describe el contrato que toda estrategia nueva debe cumplir.

---

## 1. Visión general — motor "reloj por suscripción" (D051)

El principio de diseño es **"motor tonto, estrategia lista"**:

- **El motor es tonto.** No conoce ventanas de sesión, ni `daily lull`, ni skip-Friday, ni rollover. Lo único que sabe es **cuándo cierra una vela**. Cada estrategia **declara los timeframes que le interesan** (`timeframes`, p.ej. `{"M15"}`) y el motor hace tick en la **unión** de esos límites de vela. En cada cierre de vela relevante, el motor llama a `strategy.on_bar(...)` y obedece la `Decision` que devuelve.

- **Las estrategias son listas.** Cada una lleva su propio timing, su ventana, su estado per-par, sus params y sus reglas especiales. El `SessionState` del Lull (que hoy vive suelto en `main.py`) pasa a ser estado privado de la estrategia.

**Reparto de responsabilidades (D051):**

| Responsabilidad | Dueño |
|---|---|
| Reloj y sleep stop-aware | Motor |
| Fetch de velas (dedup por (par, timeframe) por tick) | Motor (`MarketData`) |
| Gating de riesgo (global + por estrategia) | Motor |
| Position sizing y atribución por magic | Motor |
| Ejecución, logging, notificación, detección de cierres | Motor |
| Threads de monitoreo y Telegram | Motor |
| Lógica de ventana / timing | Estrategia |
| Estado per-par | Estrategia |
| Parseo de sus propios params | Estrategia |
| Reglas especiales (skip-Friday, time-stop, espera de rollover) | Estrategia |
| Generar la señal (SL/TP incluidos) | Estrategia |

**Por qué (HC3):** el timing es heterogéneo. Una trend-following futura sería 24/5 en H1 sin ventana; un motor que asuma la ventana del Lull no escala. Un solo reloj con estrategias como objetos es más simple que un thread por estrategia (que pelearían por el lock de riesgo/DB — ver alternativas descartadas en D051).

---

## 2. El contrato `Strategy` (`drift/strategies/base.py`)

Toda estrategia es una clase que cumple el `Protocol`/`ABC` definido en `drift/strategies/base.py`. Las firmas siguientes son **pseudo-código tipado ilustrativo** — refinar contra el `base.py` real cuando exista.

### 2.1 Atributos y método requeridos

```python
class Strategy(Protocol):
    name: str                  # clave de config, valor de la columna DB `strategy`,
                               #   y clave del registry. Los tres DEBEN coincidir.
    pairs: list[str]           # pares que opera esta instancia (vienen de su config)
    timeframes: frozenset[str] # timeframes que necesita, p.ej. {"M15"} o {"M15", "H4"}.
                               #   El motor tickea en la unión de timeframes de todas
                               #   las estrategias activas.

    def on_bar(
        self,
        pair: str,
        timeframe: str,           # cuál de sus timeframes acaba de cerrar
        bar_close_time: datetime, # cierre de la vela, tz-aware (ver nota de hora abajo)
        market: MarketData,       # accessor de datos servido por el motor
        ctx: StrategyContext,     # estado/atribución de ESTA estrategia
    ) -> Decision: ...
```

> ⏰ **Nota de hora.** Como en el Lull (ver `drift/strategy.py`), los timestamps de vela llegan en **hora de servidor MT5** pero etiquetados tz-aware. La estrategia razona su ventana sobre ese reloj de servidor; nunca re-localiza. Tres capas: cálculo de ventana = hora servidor MT5, DB = UTC real, presentación = UTC-5 (ver `CLAUDE.md`).

### 2.2 El tipo `Decision`

`on_bar` devuelve exactamente uno de:

```python
Decision = Open | Close | CloseAll | NoOp

@dataclass
class Open:
    signal: Signal       # incluye action buy/sell, entry, SL, TP (ver §5)

@dataclass
class Close:
    ticket: int          # cerrar UNA posición concreta (por ticket de MT5)

@dataclass
class CloseAll:
    reason: str          # cerrar TODAS las posiciones de esta estrategia
                         #   (p.ej. time-stop del Lull a las 02:00)

@dataclass
class NoOp:              # no hacer nada este tick
    ...
```

La estrategia **solo expresa intención**. El motor decide si puede ejecutarla (gating de riesgo, límites) y cómo (sizing, magic). `Open(signal)` con un trade rechazado por riesgo no es un error de la estrategia.

El `Signal` de referencia (ver `drift/strategy.py`) lleva `action`, `pair`, timestamps, `entry_price`, `sl`, `tp`, valores de indicadores y `reason`/`rejection_reason` para el logging completo de señales aceptadas y rechazadas (principio de transparencia).

### 2.3 `MarketData` — accessor de datos (servido por el motor)

```python
class MarketData(Protocol):
    def candles(self, pair: str, timeframe: str, count: int = 100) -> pd.DataFrame: ...
        # OHLCV con DatetimeIndex tz-aware, columnas open/high/low/close/volume.
        # Lazy + cacheado: el motor baja cada (par, timeframe) UNA sola vez por tick
        #   (dedup), aunque varias estrategias o varias llamadas lo pidan.
        # Devuelve solo velas cerradas (ver closed_bars / D045): la vela en formación
        #   NO se sirve, para no disparar intra-bar.
```

Una estrategia que necesite H4 para un filtro (como el ADX del Lull) lo pide con `market.candles(pair, "H4", count=50)`. **No** tiene que declarar H4 en `timeframes` si solo lo lee como contexto y no quiere ticks en cierre H4 — `timeframes` controla *cuándo te llaman*, `market.candles` controla *qué datos lees*.

### 2.4 `StrategyContext` — atribución de ESTA estrategia (servido por el motor)

```python
class StrategyContext(Protocol):
    account_balance: float          # balance total de la cuenta
    allocated_capital: float        # notional asignado a ESTA estrategia
                                    #   = account_balance * allocation_pct/100 (D053)
    open_positions: list[Position]  # posiciones abiertas de ESTA estrategia
                                    #   (filtradas por su magic = base + offset)
    paused: bool                    # True si esta estrategia está pausada
                                    #   (brake propio o /pause <nombre>)
```

El `ctx` está pre-filtrado por estrategia: `open_positions` ya excluye las de otras estrategias (filtro por magic). La estrategia razona como si fuera la única en la cuenta, sobre su capital notional.

---

## 3. Estado per-par (privado de la estrategia)

Cada estrategia gestiona **su propio estado**, igual que el `SessionState` del Lull, keyed por par. El motor **no lo toca, no lo conoce, no lo persiste** (salvo lo que la estrategia escriba ella misma vía DB; el `strategy_state` de D056 es solo `peak_equity`/`paused` para el riesgo, no el estado de lógica).

Patrón recomendado (como en `drift/strategy.py`):

```python
class MiEstrategia:
    def __init__(self, params: MiParams, pairs: list[str], ...):
        self._state: dict[str, MiEstadoPorPar] = {p: MiEstadoPorPar() for p in pairs}

    def on_bar(self, pair, timeframe, bar_close_time, market, ctx) -> Decision:
        state = self._state[pair]
        # ... mutar state en sitio, decidir, devolver Decision
```

El estado vive en memoria del proceso. Tras un reinicio se reconstruye desde cero a partir de las velas (igual que el Lull re-acumula su rango); la **verdad de posiciones abiertas** se reconstruye desde MT5 por magic (D052), no desde el estado en memoria.

---

## 4. Cómo encaja la config (D054)

El `config.yaml` pasó de `strategy:` (bloque plano) a `strategies:` (mapa por nombre). Lo compartido (broker, telegram, reports) queda arriba; el riesgo global se llama `risk_global:`; `pairs` baja a cada estrategia.

```yaml
strategies:
  daily_lull:
    enabled: true
    magic_offset: 0          # magic efectivo = system.magic_number (234000) + offset
    allocation_pct: 100      # suma de allocations de TODAS ≤ 100% (validado)
    pairs: [AUDNZD, EURCHF, EURJPY, GBPJPY, EURGBP]
    risk: { percent_per_trade: 1.0, max_open_trades: 4, max_drawdown_percent: 10.0 }
    params: { rsi_oversold: 35.0, ... }   # OPACO para el motor
```

Reglas clave:

- **`params` es opaco para el motor.** Cada estrategia define **su propio dataclass de params** (el `StrategyConfig` actual del Lull → `DailyLullParams`) y parsea su dict `params`. El motor solo conoce `enabled`, `magic_offset`, `allocation_pct`, `pairs`, `risk`. Una estrategia nueva mete sus params sin tocar config global.
- **Registry nombre → clase**, p.ej. `{"daily_lull": DailyLullStrategy}`. Un nombre en config que no esté en el registry = error de validación. El `name` de la clase, la clave de config y la clave del registry deben coincidir (también es el valor de la columna `strategy` en DB, D056).
- **`enabled: false`** desactiva sin borrar (A/B, apagado vía config + restart).
- **Migración manual.** No hay loader retrocompat; el `config.yaml` live se edita a mano una vez.

---

## 5. Riesgo y atribución desde la estrategia (D052 / D053)

Regla central: **la estrategia NO hace riesgo.** No calcula position size, no consulta drawdown, no chequea límites de trades ni de correlación. Solo emite `Open(signal)` con su `entry`, `sl` y `tp`. El motor hace todo lo demás con el `ctx`:

- **Sizing (D053):** notional. `risk_usd = ctx.allocated_capital * (percent_per_trade/100)`; de ahí el lotaje según la distancia al SL que trae el `signal`. `percent_per_trade` es **por estrategia** (vive en su bloque `risk`).
- **Atribución (D052):** el motor abre la orden con `magic = system.magic_number (234000) + magic_offset`. Eso es lo que reconstruye "de quién es esta posición" tras reinicio o desincronización de DB (MT5 es la fuente de verdad). El Lull usa `magic_offset: 0` → magic 234000, idéntico a hoy, para adoptar sus posiciones demo vivas sin huérfanos. `_validate` rechaza dos estrategias con el mismo magic efectivo.
- **Límites en dos niveles (D053):**
  - `max_open_trades`: cap global de cuenta **y** cap por estrategia (≤ global).
  - `max_drawdown_percent`: brake global (kill switch que pausa TODO) **y** brake por estrategia (pausa solo esa). El drawdown por estrategia usa su curva de equity: `allocated_capital_baseline + P&L_realizado(DB) + P&L_flotante(posiciones por su magic)`, con `peak_equity` persistido en `strategy_state` (D056).
  - `max_same_currency_direction` (correlación): **global** — es riesgo de cuenta sin importar quién abrió.
- **Pausa = mantener posiciones** en ambos niveles. Pausada, una estrategia deja de abrir nuevos pero conserva los abiertos protegidos por su SL/TP en servidor. No se cierra a mercado al pausar (materializaría la pérdida en el peor momento, especialmente perverso en mean reversion).

En resumen: el `signal` define **la idea del trade** (dirección, entry, SL, TP); el motor define **cuánto y de quién**.

---

## 6. Backtest (D055) — `on_bar` es la ÚNICA fuente de verdad

**La MISMA `on_bar` corre en live y en backtest.** No hay segunda implementación.

- **Live:** el motor llama `on_bar` en cada cierre real de vela.
- **Backtest:** un **adaptador propio** (loop simple sobre el histórico) recorre las barras y llama al *mismo* `on_bar`, pasándole un `MarketData` y un `ctx` **falsos** que sirven datos históricos y simulan la cuenta. Las métricas (PF, Sharpe, DD) se calculan sobre la curva de equity resultante. Se abandona Backtesting.py y se reemplaza `backtest/lull_engine.py` (que hoy es una segunda implementación portada a mano, con riesgo de divergencia).

> ⛔ **Regla DURA del contrato.** Una estrategia **NUNCA** llama a MT5 (ni a `mt5_client`, ni a `MetaTrader5`, ni a la DB, ni al reloj del sistema) directamente. **Todo dato entra por `market`; todo contexto entra por `ctx`.** Si la estrategia toca MT5 a mano, el adaptador de backtest no puede inyectar datos históricos y la equivalencia live↔backtest se rompe. Esta regla es lo que hace que `on_bar` sea testeable y backtesteable.

- **Tests de equivalencia:** mismas señales sobre el mismo histórico, baratos en paper (HC4).
- **Portfolio backtest** (varias estrategias sobre una curva de equity compartida) se **difiere** hasta que exista la estrategia #2.

---

## 7. Checklist — cómo añadir una estrategia nueva

1. **Crear el módulo** `drift/strategies/<nombre>.py` con la clase de la estrategia que cumple el contrato de `base.py` (§2): `name`, `pairs`, `timeframes`, `on_bar`.
2. **Definir su dataclass de params** (análogo a `DailyLullParams`), con defaults, y el parseo desde el dict `params` crudo (§4). El motor no entiende estos params; son privados.
3. **Implementar el estado per-par** privado (§3), keyed por par, mutado en sitio en `on_bar`.
4. **Implementar `on_bar`** sin tocar MT5/DB/reloj directamente: todo dato por `market`, todo contexto por `ctx` (§6). Devolver `Open`/`Close`/`CloseAll`/`NoOp`. El `Signal` lleva entry, SL y TP; el riesgo lo hace el motor (§5).
5. **Registrarla en el registry** nombre → clase. El `name` de la clase = clave de registry = clave de config = valor de columna `strategy` en DB.
6. **Añadir su bloque en `config.yaml`** (y en `config.example.yaml`):
   - `enabled: true`
   - `magic_offset` **único** (no colisiona con otra estrategia; `_validate` lo verifica).
   - `allocation_pct` respetando que la **suma de todas ≤ 100%**.
   - `pairs`, bloque `risk` (`percent_per_trade`, `max_open_trades`, `max_drawdown_percent`), bloque `params`.
7. **Añadir tests:** unit tests de `on_bar` (señales aceptadas y rechazadas con sus `reason`) y, si portas una estrategia existente, test de equivalencia contra el histórico (§6).
8. **Correr el backtest** con el adaptador genérico (mismo `on_bar`) y revisar PF/Sharpe/DD sobre la curva de equity.
9. **Actualizar docs de usuario** si la estrategia añade comandos, params o comportamiento visible (`docs/user/`). Un feature sin docs no está completo (ver `CLAUDE.md`).
10. **Documentar las decisiones de diseño** propias de la estrategia en `docs/DECISIONS.md` y, si tiene reglas no triviales, un doc de estrategia en `docs/knowledge/` (como `daily-lull-scalper.md`).

---

## 8. El Daily Lull como ejemplo de referencia

El Daily Lull es la instancia #1 y el patrón a copiar. Su lógica de estrategia (reglas, parámetros, tiempos) está en `docs/knowledge/daily-lull-scalper.md`; aquí solo importa **cómo implementa el contrato**:

- **`timeframes = frozenset({"M15"})`.** El motor lo tickea en cada cierre M15. El H4 (para el filtro ADX) lo lee como contexto vía `market.candles(pair, "H4", ...)`, no como timeframe de tick.
- **`on_bar` hace todo internamente** según la hora de servidor del `bar_close_time`:
  - **21:00–22:59 — define-range:** acumula high/low de cada vela M15 en el estado per-par; ninguna entrada. → `NoOp`.
  - **23:00 — lock:** fija el rango si pasa el filtro de ancho (1.0–4.0× ATR).
  - **23:00–01:59 — trading:** con rango lockeado, evalúa entradas (toque de extremo + RSI + filtro ADX H4), máximo una por sesión por par. → `Open(signal)` con SL/TP, o `NoOp` con `rejection_reason`.
  - **02:00 — time-stop:** fuerza el cierre de cualquier posición abierta y resetea el estado de sesión. → `CloseAll(reason="session_end_time_stop")`.
  - **02:01–20:59 — fuera de ventana:** → `NoOp`.
  - **Reglas especiales internas:** skip-Friday, espera de rollover (entradas a 00:00 hora servidor fallan con retcode 10018 por el rollover diario de ICMarkets — ver memoria del proyecto). Todo esto es **lógica del Lull**, no del motor.
- **Estado per-par:** el `SessionState` (`session_date`, `high`/`low`, `locked`, `range_high`/`range_low`, `traded`) deja de vivir en `main.py` y pasa a ser estado privado de la clase.
- **Params:** el `StrategyConfig` actual (`rsi_oversold`, `adx_max_threshold`, `range_atr_min/max`, `sl_atr_mult`, periodos de indicadores) se convierte en `DailyLullParams`, parseado desde el dict `params`.
- **Magic:** `magic_offset: 0` → magic efectivo 234000 (idéntico a hoy, adopta sus posiciones demo vivas).

**Dónde vivirá:** `drift/strategies/daily_lull.py` (hoy la lógica está en `drift/strategy.py`, que se porta al contrato). Las reglas de la estrategia en sí: `docs/knowledge/daily-lull-scalper.md`.

---

## 9. Lecciones del Daily Lull en vivo (leer ANTES de diseñar una estrategia nueva)

El Daily Lull corrió ~2 semanas en demo (jun-2026) con resultado **consistentemente negativo** (PF 0.01, 1W/6L) y terminó **sin abrir trades varios días seguidos**. La auditoría (2026-06-19) encontró una **única causa raíz** con varias consecuencias. No son bugs del motor — son lecciones de diseño de estrategia que la próxima estrategia debe respetar desde el día cero.

### L1 — El reward (TP) debe sobrevivir al spread + slippage REALES, no a los del backtest

Causa raíz de todo. El Daily Lull pone el **TP en el punto medio del rango**, lo que da un reward minúsculo (6–25 pips) y un **R:R estructuralmente < 1:1** (riesgo 2.5× ATR vs reward 0.5–2.0× ATR). En la franja del *daily lull* (23:00–02:00 server) el spread se ensancha por baja liquidez, y para cuando la orden se ejecuta el **fill ya llegó al TP o lo pasó**:

```
06-16 EURGBP  fill=0.86462 = TP exacto       → reward_left = 0.000
06-17 AUDNZD  fill=1.21722 > TP 1.21613       → reward_left negativo (entrás perdiendo)
```

- **Síntoma "sin trades":** el spread guard [[D046]] (correcto) abandona toda orden cuyo reward-tras-spread < 50% → ninguna entra.
- **Síntoma "negativos":** las pocas que entraban las cortaba el time-stop de las 02:00 antes de que la reversión completara → muerte por mil cortes.

**Regla para la estrategia nueva:** dimensioná el TP contra el **spread+slippage del par y la hora reales**, no contra el fill sin costo del backtest. Si el reward esperado es del orden del spread, la estrategia no tiene edge en vivo aunque el backtest lo muestre. Preferí **horarios líquidos**, **pares de spread bajo**, o **TPs más grandes** (R:R ≥ 1:1). El backtest **debe** modelar el costo de ejecución (ver el aviso de D055/§6: el backtest llena sin spread; [[D046]] es justo lo que esa brecha mide).

### L2 — El backtest sobreestima el edge si no modela el costo de ejecución

D043/D046 ya habían medido que el edge del Daily Lull caía de **+8.75% bruto** a **+3-5% anual** con spread realista, y que **47% de los trades** caían en la ventana rollover. El vivo confirmó el extremo pesimista. **Desconfía de un backtest sin costos de ejecución**; revalidá el edge con spread inflado en las horas/pares objetivo antes de ir a demo, y a demo antes de real.

### L3 — Un time-stop duro puede matar la tesis de la estrategia

El cierre forzado a las 02:00 corta las mean-reversion antes de que reviertan: entradas a las 01:00 server tienen 1h para funcionar. Si tu estrategia necesita tiempo para que la tesis se cumpla, **el time-stop tiene que dar ese tiempo** (o no tener time-stop). Cruzá la duración típica del trade ganador (backtest) contra la ventana disponible.

### L4 — Toda decisión silenciosa necesita un heartbeat observable

El guard `if closed == 0: return` silenciaba el "SESSION CLOSED" en noches sin trades, haciendo **indistinguible** "bot sano sin setups" de "bot colgado". Corregido en [[D066]]: el cierre de sesión **siempre** notifica. **Regla:** ningún camino normal del bot (cierre de sesión, skip de entrada por N días) debe quedar 100% mudo; siempre dejá una señal de vida (Telegram calmo o, como mínimo, log).

### L5 — La estabilidad del entorno importa tanto como el código

Parte del "no veo mensajes" eran fallas de red del entorno (`getaddrinfo failed` a Telegram, `IPC timeout` de MT5 corriendo en máquina de casa), no solo el bug L4. **Ir a VPS (Phase 3) antes de confiar en la operación 24/5.** Las notificaciones que fallan en red **no se reintentan** hoy.

> **TL;DR para la estrategia nueva:** R:R ≥ 1:1 que sobreviva al spread real del par/hora · backtest con costos de ejecución · time-stop que respete la duración del trade ganador · heartbeat en todo camino silencioso · VPS para 24/5. Estas lecciones salen de la auditoría 2026-06-19 y de [[D043]]/[[D046]]/[[D066]].

---

## Referencias

- **Decisiones:** D050 (pivote), D051 (contrato + motor), D052 (magic), D053 (riesgo dos niveles), D054 (config), D055 (backtest), D056 (DB), D057 (Telegram) — `docs/DECISIONS.md`.
- **Contrato:** `drift/strategies/base.py` (donde vivirá el `Protocol`/`ABC` y los tipos `Decision`/`MarketData`/`StrategyContext`).
- **Estrategia de referencia:** `drift/strategies/daily_lull.py` (port de `drift/strategy.py`), reglas en `docs/knowledge/daily-lull-scalper.md`.
- **Arquitectura y schema:** `docs/ARCHITECTURE.md`. **Estado del proyecto:** `PROGRESS.md`, `ROADMAP.md` (Phase 2.5).
