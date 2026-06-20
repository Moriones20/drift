# Comandos de Telegram

El bot acepta comandos unicamente desde el chat configurado en `telegram.chat_id`. Cualquier otro chat es ignorado silenciosamente.

> ⚠️ Esquema multi-estrategia (en implementacion — ver D050-D057)
>
> Drift es ahora una plataforma que hospeda N estrategias sobre una sola cuenta. Los comandos de consulta (`/status`, `/balance`, `/trades`, `/report`) muestran el **agregado de cuenta** mas un **desglose por estrategia**. `/pause` y `/resume` aceptan un nombre de estrategia opcional, y hay un nuevo comando `/strategies`. `/stop` sigue siendo global.

---

## /status

Muestra el estado actual del bot en tiempo real.

**Informacion que devuelve:**
- Estado del bot: corriendo o pausado.
- Conexion con MT5: conectado o desconectado.
- Tiempo activo desde el inicio (uptime).
- Cantidad de trades abiertos actualmente (total de la cuenta).

**Desglose por estrategia:** para cada estrategia activa, su estado (corriendo o pausada) y su numero de trades abiertos.

**Cuando usarlo:** para verificar rapidamente que el bot esta operando normalmente, especialmente despues de reiniciarlo o tras una pausa.

---

## /trades

Lista todos los trades abiertos en este momento.

**Informacion por trade:**
- Estrategia que abrio el trade.
- Par y direccion (BUY o SELL).
- Precio de entrada.
- Stop loss y take profit.
- Tamaño en lotes.
- P&L flotante actual (si esta disponible).
- Hora de apertura.

Los trades se presentan agrupados por estrategia, con el agregado de la cuenta al final.

**Cuando usarlo:** para revisar las posiciones activas y sus niveles de riesgo.

---

## /history

Muestra los ultimos 5 trades cerrados.

**Informacion por trade:**
- Par y direccion.
- Precio de entrada y salida.
- P&L realizado.
- Motivo de cierre: `stop_loss`, `take_profit`, `session_close` (cierre forzado 02:00 hora servidor MT5 = 18:00 Bogota), `manual` (cerrado via `/stop`), o `drawdown_pause`.
- Duracion del trade.
- Hora de cierre.

**Cuando usarlo:** para revisar el desempeno reciente y entender por que se cerraron los ultimos trades.

---

## /balance

Muestra el estado financiero de la cuenta.

**Informacion que devuelve (agregado de cuenta):**
- Balance actual de la cuenta (obtenido en tiempo real desde MT5).
- Balance pico historico desde que el bot inicio.
- Drawdown actual en porcentaje respecto al pico (relevante para el kill switch global).
- P&L total acumulado de todos los trades cerrados.

**Desglose por estrategia:** para cada estrategia, su capital asignado (notional), su P&L acumulado y su drawdown propio respecto a su pico de equity individual (relevante para su brake por estrategia).

**Cuando usarlo:** para monitorear la salud financiera de la cuenta y el drawdown, tanto a nivel global como por estrategia.

---

## /pause

Pausa el bot: deja de buscar nuevas señales y no abre nuevos trades. Acepta un nombre de estrategia opcional.

**Uso:**
- `/pause` (sin argumento) → pausa **toda la cuenta** (todas las estrategias).
- `/pause daily_lull` → pausa **solo** esa estrategia; las demas siguen operando.

**Comportamiento (en ambos casos):**
- Los trades abiertos de lo pausado se mantienen con sus stop loss y take profit activos (pausar nunca cierra posiciones; ver D053).
- El hilo de monitoreo sigue corriendo: se detectan cierres por SL/TP y se verifica el drawdown.
- El bot responde a comandos de Telegram normalmente.
- Si lo indicado ya esta pausado, informa que ya lo esta. Si el nombre de estrategia no existe, lo informa.

**Cuando usarlo:** ante noticias economicas importantes, condiciones de mercado inusuales, o cuando se quiere revisar algo (de una estrategia o de toda la cuenta) sin apagar el bot completamente.

Para reanudar, usar `/resume`.

---

## /resume

Reanuda el bot despues de una pausa. Acepta un nombre de estrategia opcional, en simetria con `/pause`.

**Uso:**
- `/resume` (sin argumento) → reanuda **toda la cuenta**.
- `/resume daily_lull` → reanuda **solo** esa estrategia.

**Comportamiento:**
- Lo reanudado vuelve a analizar señales en el siguiente cierre de vela correspondiente (para el Daily Lull, una vela M15 dentro de su ventana activa, 23:00-01:59 hora servidor MT5 = 15:00-17:59 Bogota).
- Funciona tanto para pausas manuales (via `/pause`) como para pausas automaticas por drawdown (kill switch global o brake por estrategia).
- Si lo indicado no esta pausado, informa que ya esta corriendo. Si el nombre de estrategia no existe, lo informa.

**Cuando usarlo:** despues de un `/pause` manual, o despues de revisar y aceptar la situacion tras una pausa automatica por drawdown.

---

## /stop

Cierra todos los trades abiertos y apaga el bot completamente. **Siempre es global** — no acepta nombre de estrategia.

**Comportamiento:**
- Cierra todas las posiciones abiertas al precio de mercado actual, de todas las estrategias.
- Detiene el loop principal, el hilo de monitoreo y el bot de Telegram.
- El proceso termina. Se debe reiniciar manualmente con `python main.py`.

**Cuando usarlo:** para apagar el bot de forma controlada, especialmente antes de hacer cambios en la configuracion o actualizar el codigo. No usarlo como pausa — para eso esta `/pause`. Para detener una sola estrategia sin apagar el bot, usar `/pause daily_lull`.

---

## /strategies

> ⚠️ Esquema multi-estrategia (en implementacion — ver D050-D057)

Lista todas las estrategias configuradas con su estado operativo, a modo de panel de control de la plataforma.

**Informacion por estrategia:**
- Nombre de la estrategia.
- `enabled`: si esta activada en la config (`true`/`false`).
- `paused`: si esta pausada en este momento (manual o por su brake de drawdown).
- `allocation_pct`: su asignacion notional de capital.
- Magic efectivo en MT5 (base + offset; p.ej. 234000 para el Daily Lull).
- Trades abiertos actualmente.
- Drawdown actual respecto a su pico de equity propio.

**Cuando usarlo:** para ver de un vistazo que estrategias estan vivas, cuales estan pausadas y como va el riesgo de cada una.

---

## /report

Genera un reporte de rendimiento completo bajo demanda.

**Informacion que devuelve (agregado de cuenta):**
- Trades abiertos en este momento.
- Total de trades cerrados.
- Ganadores y perdedores.
- Win rate (porcentaje de trades ganadores).
- Profit factor (ganancia bruta / perdida bruta).
- P&L total acumulado.
- Duracion promedio de los trades.

**Desglose por estrategia:** las mismas metricas (trades abiertos, cerrados, win rate, profit factor, P&L, drawdown) calculadas para cada estrategia por separado, ademas del agregado.

**Cuando usarlo:** para evaluar el desempeno historico del bot en cualquier momento, a nivel global y por estrategia. El reporte cubre todos los trades registrados en la base de datos desde el inicio.

---

## Notificaciones automaticas

Ademas de los comandos, el bot envia notificaciones de forma automatica en los siguientes eventos:

| Evento | Descripcion |
|---|---|
| Inicio del bot | "BOT ONLINE" (cohete): el proceso arranco. Muestra el balance y que estrategias quedaron activas. Distinto del inicio de sesion. |
| Inicio de sesion | Aviso calmo ("SESSION OPEN", icono luna) cuando la estrategia entra en su ventana (21:00 hora servidor) y empieza a vigilar setups. Una sola notificacion por estrategia por sesion. NO es un arranque del bot. |
| Cierre de sesion | Aviso calmo ("SESSION CLOSED", icono dormir) cuando la estrategia cierra la sesion al time-stop (02:00 hora servidor). Indica cuantas posiciones cerro, o "no trades this session" si la noche fue tranquila, y que duerme hasta la proxima sesion. Se envia **siempre** a las 02:00 como heartbeat de vida, incluso sin trades (D066). Una sola notificacion por estrategia por cierre (no una por par). NO es un BOT STOPPED: el bot sigue vivo. |
| Trade abierto | Par, direccion, precio de entrada, SL, TP, tamaño en lotes y riesgo en USD. |
| Trade cerrado | Par, direccion, precio de entrada y salida, P&L, motivo de cierre y duracion. |
| Freno por drawdown | Alarma (icono 🚨), no una pausa calma. "ACCOUNT HALTED — DRAWDOWN" cuando el kill switch global frena toda la cuenta; "STRATEGY HALTED — DRAWDOWN" cuando solo se frena una estrategia (las demas siguen). Indica como reanudar (/resume o /resume <estrategia>). |
| Error en el loop | Si ocurre un error inesperado en el loop principal, el bot se pausa y notifica para que se investigue. |
| Reconexion a MT5 | "MT5 RECONNECTED" (icono enchufe) cuando se recupera la conexion. Si se pierde y no logra reconectar, llega un ERROR explicando que no se abriran trades y que seguira reintentando. NO se muestra como un arranque del bot. |
| Cierre del bot | Confirma que el bot se apago correctamente. |
| Reporte semanal | Enviado automaticamente segun la config (`reports.weekly_report_day` / `weekly_report_hour` / `timezone`). Con los valores por defecto: domingo a las 20:00 UTC-5 (lunes 01:00 UTC). Mismo contenido que `/report`, incluyendo una **seccion por estrategia** ademas del agregado de cuenta. |
