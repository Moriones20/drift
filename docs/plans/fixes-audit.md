> ⚠️ PLAN EJECUTADO / ARCHIVADO (2026-06-02). Conservado como histórico. Notas: (a) El Paso 1 proponía NSSM como servicio Windows; se descartó — producción usa **Windows Task Scheduler** (ver docs/user/runbook.md y D037-era ops). (b) El Paso 11 (D1-D3: headers de deprecación, eliminación de friday_close_hour_utc, sellos 'SUPERSEDIDA por D029') ya está completo.

# Plan de Fixes — Auditoría de Seguridad/Bugs (2026-05-31)

Plan ejecutable derivado de una auditoría completa del bot Drift, **re-auditando cada hallazgo
con evidencia** (código real, log en vivo, estado del proceso, datos del broker vía MT5 conectado,
versiones instaladas). Varios hallazgos del informe inicial fueron **refutados** en la re-auditoría
y otros **nuevos** se descubrieron en el proceso.

> Contexto operativo crítico (registrado en memoria): el usuario **suspende** (no apaga) el PC los
> viernes ~20:00-22:00 local y lo **reanuda** los domingos ~18:00 local (UTC-5). El proceso se
> congela durante el suspend; MT5 muere y debe reconectar al reanudar. Esto afecta N2, N3 y N4.

> **Estado al momento de la auditoría:** el bot estaba **CAÍDO** — murió ~18:11 local (23:11 UTC)
> del 2026-05-31 sin dejar traza, después de reconectar MT5 y antes del tick de las 23:15. No hay
> trades abiertos, así que no hubo riesgo inmediato. Esto motiva el paso 1 (N4).

---

## Hallazgos refutados (NO se actúa — documentados para no re-litigar)

- **C1 (doble init de Telegram)** — REFUTADO. En PTB 22.7 `initialize()` es idempotente (no-op si
  ya inicializado) y `run_polling()` es síncrono. El bot lleva días corriendo con Telegram sano
  (verificado en `logs/drift.log` y con el proceso en vivo). El único residuo es un `TypeError`
  cosmético en el shutdown por envolver `run_polling()` (síncrono) en `run_until_complete`. Ver paso 4.
- **B2 (TP viola `trade_stops_level`)** — REFUTADO. `trade_stops_level = 0` en los 5 pares de
  ICMarkets (verificado vía `symbol_info`). El broker no impone distancia mínima de SL/TP. Degradado
  a higiene cosmética (redondeo a digits) → paso 10.

---

## Checklist manual del usuario (NO código — hacer antes/independiente del plan)

- [ ] **Rotar el token de Telegram** con @BotFather (`/revoke` → nuevo token) — estuvo expuesto
      13.199 veces en `logs/drift.log`. Actualizar `config.yaml` con el nuevo token. (Paso 2 elimina
      la fuga futura, pero el token actual ya está comprometido.)
- [ ] **Cambiar la contraseña** de la cuenta demo ICMarkets (texto plano en `config.yaml`).
- [ ] **N3 — Encender el PC los domingos antes de las 16:00 local (21:00 UTC)** — idealmente ~15:30.
      La estrategia define el rango entre 21:00-22:59 UTC; encender a las 18:00 local (23:00 UTC) se
      pierde toda la ventana de definición y **la sesión del domingo no opera**. Sin fix de código;
      es un cambio de hábito.

---

## Orden de ejecución y dependencias

Los pasos están ordenados por severidad/urgencia. Pasos 1-2 son críticos. Pasos independientes entre
sí salvo donde se indica. **El paso 3 (stack) requiere una decisión basada en observar una sesión real
antes de tocar nada** — ver su nota.

| Paso | ID | Severidad | Archivos | Depende de |
|---|---|---|---|---|
| 1 | N4 | 🔴 | (nuevo) `scripts/`, `main.py` | — |
| 2 | S1b | 🔴 | `main.py` | — |
| 3 | C2 | 🟠 | `requirements.txt`, `drift/indicators.py` | decisión post-observación |
| 4 | N1 | 🟠 | `drift/telegram_bot.py`, `main.py` | — |
| 5 | B1 | 🟠 | `drift/mt5_client.py`, `drift/risk.py`, `main.py` | — |
| 6 | B4+N2 | 🟠 | `drift/mt5_client.py`, `main.py` | — |
| 7 | B3 | 🟡 | `main.py` | — |
| 8 | B5 | 🟡 | `drift/trailing.py` | — |
| 9 | B6 | 🔵 | `main.py` | — |
| 10 | B2 | 🔵 | `drift/executor.py` | — |
| 11 | D1-D3 | 🔵 | `CLAUDE.md`, `config.yaml`, `config.example.yaml`, `docs/DECISIONS.md`, `docs/knowledge/trend-following-indicators.md` | — |

---

## Paso 1 — N4: Supervisión de proceso (NSSM) + error handler global 🔴

**Problema:** No hay supervisión. El bot se lanza con `nohup python main.py &` (runbook) sin nada que
lo reinicie. Cuando murió hoy (~18:11), se quedó muerto 2h+. Para un bot que maneja dinero, un proceso
caído = posiciones sin gestión, time-stop de las 02:00 sin ejecutar, cierres sin detectar.

**Fix A — NSSM (Non-Sucking Service Manager) como servicio Windows:**

1. Documentar/scriptar la instalación del servicio. Crear `scripts/install-service.ps1`:
   - Descargar/ubicar `nssm.exe` (documentar en runbook si requiere instalación manual).
   - `nssm install Drift "<python.exe>" "<ruta>\main.py"` con `AppDirectory` = raíz del repo.
   - Configurar restart: `AppExit Default Restart`, `AppRestartDelay 10000` (10s, evita restart-loop
     apretado), y throttle para que crashes inmediatos no martilleen.
   - Redirigir stdout/stderr a `logs/service-stdout.log` / `logs/service-stderr.log`.
   - Arranque automático: `nssm set Drift Start SERVICE_AUTO_START` (revive al encender el PC).
2. Documentar en `docs/user/runbook.md` los comandos: `nssm start/stop/restart/status Drift`, y cómo
   reemplaza al `nohup` actual.

**Fix B — error handler global en `main()` (`main.py`):**

El loop principal ya captura excepciones (línea ~1017) pero el crash de hoy NO dejó traza → fue muerte
dura (probable segfault en extensión C: pandas/numpy/MT5). Reforzar:
- Envolver el cuerpo de `main()` en un `try/except BaseException` de último recurso que, ante cualquier
  excepción no capturada: loguee la traza completa (`logger.exception`), intente notificar por Telegram
  (`notify_error`) de forma síncrona-con-timeout, registre un evento `"error"` en DB con detalle, y
  re-lance para que NSSM reinicie. Esto garantiza evidencia en el próximo crash de Python.
- Nota: un segfault nativo NO lo captura Python; para eso está NSSM (reinicia el proceso muerto). Los
  dos mecanismos son complementarios: error handler para crashes de Python (deja traza), NSSM para
  muerte dura (reinicia).

**Verificación:**
- `nssm status Drift` → `SERVICE_RUNNING`.
- Matar el proceso manualmente (`taskkill /F`) y confirmar que NSSM lo relanza en ~10s.
- Forzar una excepción de prueba en un punto temporal y confirmar traza en log + notificación Telegram.

**Limitación conocida (no resuelve N3):** NSSM reinicia procesos *muertos*, no descongela procesos
*suspendidos*. Tras un resume, el proceso sigue vivo (congelado) con MT5 muerto → eso lo cubre el
paso 6 (reconnect), no NSSM.

---

## Paso 2 — S1b: Eliminar la fuga del token de Telegram en logs 🔴

**Problema:** El logger de `httpx` está a nivel INFO y vuelca la URL completa de cada request, que
incluye el bot token embebido: `POST https://api.telegram.org/bot<TOKEN>/getUpdates`. Verificado:
**13.199 ocurrencias** del token en `logs/drift.log`. Los logs se comparten para depurar, se respaldan
y se suben al VPS → fuga activa del token.

**Fix (`main.py`, en `_configure_logging()` ~línea 68-88):**
- Subir el logger de `httpx` (y opcionalmente `telegram`/`httpcore` si verbosos) a WARNING:
  ```python
  logging.getLogger("httpx").setLevel(logging.WARNING)
  ```
- Esto elimina la fuga **y** ~13k líneas de ruido de un golpe (los `getUpdates` cada 10s).

**Verificación:**
- Tras reiniciar, `grep -c "<token>" logs/drift.log` sobre log nuevo → 0 (salvo el mensaje de arranque
  si lo hubiera; confirmar que ningún log de nivel INFO+ imprime la URL con token).
- Confirmar que las notificaciones de Telegram siguen funcionando (el cambio solo afecta el logging,
  no las llamadas).

**Recordatorio:** el token actual ya está comprometido → rotación manual (checklist).

---

## Paso 3 — C2: Reconciliar el stack de dependencias 🟠

**Problema:** `requirements.txt` describe un entorno que NO es el que corre:
- Pin `python-telegram-bot>=20.0,<22.0` pero instalado **22.7** (el código usa semántica de PTB 22).
- Sin pin de Python; corriendo **3.14.5** (muy nuevo).
- `pandas>=2.0` pero instalado **3.0.3** (major nuevo, Copy-on-Write obligatorio).
- `pandas-ta>=0.3.14`: instalado 0.4.71b0 pero **`import pandas_ta` FALLA** en Python 3.14
  (librería original archivada en GitHub; linaje sucesor en riesgo de discontinuación jul-2026).

Si se reinstala desde `requirements.txt` (p. ej. al montar el VPS, D015), pip instalaría PTB 21.x y
posiblemente pandas distinto → el VPS correría código distinto al validado en demo.

**Riesgo verificado por investigación (con fuentes):** "Python 3.14 + pandas 3.0 + numpy 2.4" es un
combo coherente e individualmente soportado, pero NO battle-tested para un bot de dinero desatendido.
pandas 3.0 (ene-2026) tiene CoW obligatorio que cambia comportamiento de mutaciones de DataFrame en
silencio. El crash de hoy ocurrió justo antes de que `evaluate_pair` corriera bajo pandas 3.0 por
primera vez → **la lógica de estrategia aún no se ha validado en producción bajo este stack.**

**DECISIÓN PENDIENTE (basada en datos, no teoría):**
1. **Observar primero:** dejar correr una sesión completa (próxima oportunidad: domingo encendiendo
   antes de 21:00 UTC, ver N3) y revisar el log. ¿`evaluate_pair` corre limpio bajo pandas 3.0? ¿Abre
   trades sin error de CoW?
2. **Si corre limpio** → **congelar el stack actual** con pins exactos (`pip freeze` → `==`),
   documentar "Python 3.14" en runbook/README. El VPS replicará exactamente lo validado.
3. **Si hay errores CoW/crash** → **bajar a Python 3.13 + pandas 2.3.x + numpy 2.x** (combo moderno sin
   sorpresas de CoW ni novedad de 3.14). Reinstalar entorno, re-correr backtest, diff de equity curve.

**Independiente de la decisión — quitar `pandas-ta` del runtime:**
- `compute_atr` (en `drift/indicators.py`) es el único consumidor de `pandas_ta`, y lo usa solo
  `trailing.py`, que está **apagado** (`use_trailing_stop: false`). La fórmula Wilder de `compute_atr`
  es **idéntica** a la ya implementada en `indicators.py:atr()` (pandas puro). Reimplementar
  `compute_atr` con pandas puro y eliminar la dependencia `pandas-ta` de `requirements.txt` (moverla a
  un `requirements-backtest.txt` opcional si el backtest la necesita). Esto desactiva la bomba latente:
  hoy si alguien activa el trailing, el monitoreo crashea por el import roto.

**Verificación:**
- `pip install -r requirements.txt` en un venv limpio instala exactamente las versiones esperadas.
- `python -c "from drift.indicators import compute_atr"` no importa `pandas_ta`.
- `evaluate_pair` y `compute_atr` producen los mismos valores que antes (test de regresión / diff).

---

## Paso 4 — N1: Error handler de Telegram + fix del TypeError de shutdown 🟠

**Problema:** No se registra `application.add_error_handler(...)`. Cada blip de red (DNS/sin internet)
vuelca una traza de ~20 líneas al log: **78 ocurrencias** de `No error handlers are registered`. La
causa raíz de las trazas es `httpcore.ConnectError: getaddrinfo failed` (pérdidas momentáneas de red,
p. ej. 27 min sin red el 2026-05-29). No es bug de lógica, pero el ruido enmascara errores reales.

**Fix (`drift/telegram_bot.py`, en `setup_bot`):**
- Definir un handler:
  ```python
  async def _on_error(update, context):
      logger.warning("Telegram update error: %s", context.error)
  ```
  y registrarlo: `app.add_error_handler(_on_error)`. Loguea compacto (una línea, nivel WARNING) en vez
  de la traza completa, para errores de red transitorios.

**Fix secundario — TypeError cosmético de shutdown (`main.py` `_run_telegram_thread` ~línea 785):**
- `run_polling()` es síncrono en PTB 22; envolverlo en `loop.run_until_complete(...)` causa
  `run_until_complete(None)` → TypeError al apagar. Llamar `bot_app.run_polling(...)` directo, sin
  `run_until_complete`. (Cosmético, solo en shutdown; agrupado aquí por tocar el mismo subsistema.)

**Verificación:**
- Simular fallo de red (desconectar) y confirmar que el log muestra una línea WARNING compacta, no una
  traza de 20 líneas ni `No error handlers are registered`.
- Apagar el bot limpio y confirmar que no aparece TypeError en el shutdown.

---

## Paso 5 — B1: Drawdown y peak sobre equity (no balance) 🟠

**Problema:** `get_balance()` (`mt5_client.py:53-57`) devuelve `account.balance`, que NO incluye el
P&L flotante de posiciones abiertas. Todo el sistema de drawdown (el freno de supervivencia del 10%,
D009) mide sobre balance → pérdidas no realizadas no cuentan hasta el cierre. El freno puede no
dispararse aunque el equity caiga -8% intradía.

**Fix:**
1. **`drift/mt5_client.py`:** añadir `get_equity() -> float` análoga a `get_balance()` pero retornando
   `account.equity` (con el mismo manejo de error `RuntimeError` si no conectado).
2. **Path de drawdown (freno de supervivencia):** que use equity:
   - `main.py:_check_drawdown_pause` (~690) y los puntos del monitor (~659/670) usan equity para el
     cálculo de drawdown.
   - El **peak** también debe medirse sobre equity para comparar peras con peras (equity vs equity-peak,
     no equity vs balance-peak, que dispararía el freno espuriamente). Actualizar `peak_balance_ref` y
     `log_peak_balance` para reflejar equity.
3. **`drift/risk.py:check_drawdown`** no cambia de firma (recibe `current` y `peak`); solo cambia qué
   se le pasa desde `main.py`. Mantiene `risk.py` puro.
4. Los **reportes** (`report.py`, `/balance`) pueden seguir mostrando balance Y equity para claridad,
   pero el drawdown reportado debe ser consistente con el del freno (equity).

**Verificación:**
- Con una posición abierta en pérdida flotante, `/balance` y el log de drawdown reflejan la caída de
  equity inmediatamente (no esperan al cierre).
- Tests de `check_drawdown` siguen pasando (la función no cambió).

---

## Paso 6 — B4+N2: Reconnect — cablear config + backoff progresivo 🟠

**Problema (fusión de dos hallazgos):**
- **B4:** `config.system.mt5_reconnect_interval_seconds` (300, en `config.yaml:51`) se parsea pero
  `main.py:641` llama `reconnect(config.broker, shutdown_event=...)` **sin pasar `interval=`** → usa el
  default de firma (300). Config muerta (viola D017). Hoy coincide por casualidad.
- **N2:** el intervalo de 300s se aplica **igual al primer reintento** (`mt5_client.py:78`). Tras un
  resume, el 1er `connect()` falla con "Authorization failed" (transitorio, el terminal MT5 aún revive)
  y el bot espera 300s completos → ~5 min muertos. Verificado 3 veces en el log (30-may 18:55/19:36,
  31-may 17:56); hoy se perdió el tick de las 23:00 UTC por esto.

**Fix A — cablear config + backoff progresivo (`drift/mt5_client.py:reconnect` + `main.py:641`):**
1. `main.py:641`: pasar `interval=config.system.mt5_reconnect_interval_seconds` (mata la config muerta).
2. `reconnect()`: en vez de intervalo plano, usar **backoff progresivo**: primer reintento ~5s, segundo
   ~30s, luego `interval` (300s) para los siguientes. El primer reintento rápido recupera casi
   instantáneo del suspend (caso recurrente del usuario cada domingo); el intervalo largo configurable
   cubre caídas genuinas del broker. Mantener `shutdown_event.wait()` (apagado limpio).
3. Definir `max_retries`: subir de 3 a ~4-5 para dar margen a suspends donde MT5 tarda más en revivir.
   (Valor exacto a confirmar al implementar; 4 cubre ~5.5 min con el backoff propuesto.)

**Verificación:**
- Cambiar `mt5_reconnect_interval_seconds` en config a un valor distinto y confirmar que `reconnect`
  lo respeta (ya no ignora la config).
- Simular reconexión (cerrar/reabrir MT5) y confirmar que el primer reintento ocurre en ~5s, no 300s.

---

## Paso 7 — B3: No registrar cierre hasta tener el deal 🟡

**Problema:** En `_detect_closed_trades` (`main.py:748-751`), si `history_deals_get()` vuelve vacío
(timing: el ticket desapareció de `positions_get` antes de que el deal aparezca en el historial, común
tras reconnect/suspend), el código graba `exit_price=0.0`, `pnl=0.0`, `close_reason="stop_loss"`
inventado → registro corrupto que contamina DB y reporte semanal. Aún no se ha disparado (0 trades en
DB), pero es plausible con la rutina de suspend del usuario.

**Fix C (`main.py:_detect_closed_trades`):**
- Si `history_deals_get()` vuelve vacío/None: **no registrar el cierre.** Dejar el trade como abierto en
  DB y dejar que el siguiente ciclo del monitor (cada 30s) lo reconcilie cuando el deal ya esté en el
  historial. Loguear un `logger.info` de "deal aún no disponible para ticket=X, reintentando próximo
  ciclo" para trazabilidad.
- Eliminar la rama `else` que falsea `exit_price/pnl/close_reason`.

**Consecuencia aceptada:** el trade aparece "abierto" en `/trades` ~30s extra. Es correcto: realmente
no se sabe cómo cerró hasta tener el deal.

**Verificación:**
- Simular `history_deals_get` vacío y confirmar que NO se escribe registro corrupto; en el siguiente
  ciclo (con deal presente) se registra correctamente con exit/pnl/reason reales.

---

## Paso 8 — B5: Broker = única autoridad de TP 🟡

**Problema:** El TP se pone nativo en el broker (`executor.py:38` `"tp": take_profit`) Y además
`process_open_trades` (`trailing.py:137-160`) comprueba `check_tp` y cierra manualmente cada 30s. El
`check_tp` está **antes** del guard `if not config.use_trailing_stop` (línea 163), así que corre aunque
el trailing esté apagado. Carrera: el broker cierra en TP server-side y ~30s después el monitor intenta
cerrar un ticket inexistente → error de cierre fantasma en log. O el monitor cierra primero a precio de
mercado (peor que el TP exacto).

**Fix A (`drift/trailing.py:process_open_trades`):**
- Eliminar el bloque `check_tp` → `close_trade` (líneas ~137-160). El TP nativo del broker (ya puesto en
  `open_trade`) es la autoridad. `_detect_closed_trades` ya etiqueta correctamente los cierres por TP
  vía `DEAL_REASON_TP`, así que el reporte sigue intacto.
- Resultado: con `use_trailing_stop: false`, `process_open_trades` queda como monitor puro (no cierra
  activamente, solo el broker lo hace) — diseño correcto para el Asian Scalper.

**Verificación:**
- Confirmar que un trade que alcanza TP se cierra una sola vez (server-side), sin error de cierre
  fantasma en log, y que `_detect_closed_trades` lo registra como `take_profit`.

---

## Paso 9 — B6: Validar volume_min/step/max del símbolo 🔵

**Problema:** `calculate_position_size` (`risk.py:29,31`) asume min=0.01 y step=0.01 hardcodeados. Datos
reales de ICMarkets (verificados): los 5 pares tienen `volume_min=0.01`, `volume_step=0.01`,
`volume_max=100.0` → las asunciones son correctas HOY, pero `volume_max` no se valida y el acoplamiento
implícito muerde si se cambia de broker.

**Fix B (`main.py:_analyse_pair_m15`):**
- Mantener `risk.py` puro (función matemática, testeable sin mockear MT5).
- En `_analyse_pair_m15`, donde ya se consulta `symbol_info` para el pip value, leer también
  `volume_min/volume_step/volume_max` y ajustar el `lot_size` resultante: redondear hacia abajo al
  `volume_step` real, validar contra `volume_min` (→ 0/skip si menor) y `volume_max` (→ cap o skip si
  mayor). Loguear si se ajusta.

**Verificación:**
- Test/log confirma que el lote enviado a `open_trade` respeta step/min/max del símbolo.
- `risk.py` sigue sin importar MT5; sus tests siguen pasando sin mocks.

---

## Paso 10 — B2: Redondear SL/TP a digits 🔵

**Problema (cosmético):** `open_trade` (`executor.py:31-44`) manda `sl`/`tp` como floats crudos del
cálculo ATR (ej. `1.083745123…`) sin redondear a `symbol_info.digits`. ICMarkets redondea internamente
(no rompe), pero es higiene básica. `trade_stops_level=0` confirma que no hay rechazo por distancia.

**Fix (`drift/executor.py:open_trade`):**
- Antes de construir el `request`, redondear: `sl = round(stop_loss, digits)`, `tp = round(take_profit,
  digits)`, con `digits = mt5.symbol_info(pair).digits`. (Manejar el caso `symbol_info is None` con el
  guard que ya existe para el tick.)

**Verificación:**
- Confirmar en log que sl/tp enviados tienen el número correcto de decimales (5 para no-JPY, 3 para JPY).

---

## Paso 11 — D1-D3: Reconciliar documentación 🔵

**Problema:** La estrategia cambió a Asian Session Scalper (D029) pero quedan rastros de la anterior
(trend-following EMA/MACD).

**Fixes (un solo paso de higiene documental):**

1. **D1 — CLAUDE.md:**
   - Línea 1: "forex **trend-following** bot" → "Asian Session Scalper forex bot" (o equivalente).
   - Línea 136 (prompt de implementación): "a forex **trend following** bot" → corregir.
   - Tabla de documentos (línea 15): la referencia a `trend-following-indicators.md` se mantiene pero
     marcada como histórica (ver punto 4).

2. **D2 — quitar config legacy:**
   - Eliminar `friday_close_hour_utc: 20` de `config.yaml:53` (parseada-pero-no-usada; D035 lo mandó
     eliminar).
   - Verificar y eliminar de `config.example.yaml` si está presente.

3. **D3 — marcar decisiones supersedidas en `docs/DECISIONS.md`:**
   - Añadir al encabezado de **D002** (Trend Following), **D005** (EMA 50/200 + MACD), **D008**
     (Trailing + 1:2) una nota: **"⚠️ SUPERSEDIDA por D029 — Asian Session Scalper. Conservada para
     contexto histórico."**

4. **D1 — `docs/knowledge/trend-following-indicators.md`:**
   - **Conservar** el archivo (útil si se explora la estrategia híbrida futura, D023) pero añadir un
     encabezado destacado al inicio: **"⚠️ ESTRATEGIA HISTÓRICA — ya no activa. El bot usa Asian Session
     Scalper (ver D029 y `docs/knowledge/asian-session-scalper.md`). Este documento se conserva como
     referencia para una posible estrategia híbrida futura (D023)."**

**Verificación:**
- `grep -ri "trend.following" CLAUDE.md` no presenta la estrategia como activa.
- `grep "friday_close_hour_utc" config.yaml config.example.yaml` → sin resultados.
- D002/D005/D008 y el doc de knowledge tienen el encabezado de "supersedida/histórico".

---

## Notas finales

- **Después de cada paso:** correr `ruff check . && ruff format .`, ejecutar la suite de tests
  (`python tests/test_e2e.py`, `test_strategy.py`, `test_session_fixes.py`) y commitear. Actualizar
  `PROGRESS.md`.
- **Tests a añadir/actualizar:** B1 (drawdown sobre equity), B3 (no-registro sin deal), B6 (ajuste de
  volumen), reconnect backoff (B4+N2). B5 puede requerir ajustar tests de `process_open_trades`.
- **Documentación (definición de done):** los pasos 1 (runbook NSSM), 3 (requirements/Python en
  runbook), 5/6 (configuración) tocan features de usuario → actualizar `docs/user/` correspondiente.
- **Paso 3 está bloqueado** por la observación de una sesión real bajo pandas 3.0. No congelar ni bajar
  el stack hasta tener ese dato. El resto de pasos pueden proceder en paralelo.
- **Riesgo de restart-loop:** una vez activo NSSM (paso 1), si el gatillo del crash de hoy es pandas 3.0
  en `evaluate_pair`, NSSM reiniciaría en bucle al entrar a sesión. Por eso paso 1 (supervisor) y paso 3
  (stack) son complementarios; el error handler global del paso 1 dejará la traza que falta para
  diagnosticar el crash.
