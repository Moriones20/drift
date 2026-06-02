# Comandos de Telegram

El bot acepta comandos unicamente desde el chat configurado en `telegram.chat_id`. Cualquier otro chat es ignorado silenciosamente.

---

## /status

Muestra el estado actual del bot en tiempo real.

**Informacion que devuelve:**
- Estado del bot: corriendo o pausado.
- Conexion con MT5: conectado o desconectado.
- Tiempo activo desde el inicio (uptime).
- Cantidad de trades abiertos actualmente.

**Cuando usarlo:** para verificar rapidamente que el bot esta operando normalmente, especialmente despues de reiniciarlo o tras una pausa.

---

## /trades

Lista todos los trades abiertos en este momento.

**Informacion por trade:**
- Par y direccion (BUY o SELL).
- Precio de entrada.
- Stop loss y take profit.
- Tamaño en lotes.
- P&L flotante actual (si esta disponible).
- Hora de apertura.

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

**Informacion que devuelve:**
- Balance actual de la cuenta (obtenido en tiempo real desde MT5).
- Balance pico historico desde que el bot inicio.
- Drawdown actual en porcentaje respecto al pico.
- P&L total acumulado de todos los trades cerrados.

**Cuando usarlo:** para monitorear la salud financiera de la cuenta y el drawdown.

---

## /pause

Pausa el bot: deja de buscar nuevas señales y no abre nuevos trades.

**Comportamiento:**
- Los trades abiertos se mantienen con sus stop loss y take profit activos.
- El hilo de monitoreo sigue corriendo: se detectan cierres por SL/TP y se verifica el drawdown.
- El bot responde a comandos de Telegram normalmente.
- Si el bot ya esta pausado, informa que ya lo esta.

**Cuando usarlo:** ante noticias economicas importantes, condiciones de mercado inusuales, o cuando se quiere revisar algo sin apagar el bot completamente.

Para reanudar, usar `/resume`.

---

## /resume

Reanuda el bot despues de una pausa.

**Comportamiento:**
- El bot vuelve a analizar señales en el siguiente cierre de vela M15 dentro de la ventana activa (23:00-01:59 hora servidor MT5 = 15:00-17:59 Bogota).
- Funciona tanto para pausas manuales (via `/pause`) como para pausas automaticas por drawdown.
- Si el bot no esta pausado, informa que ya esta corriendo.

**Cuando usarlo:** despues de un `/pause` manual, o despues de revisar y aceptar la situacion tras una pausa automatica por drawdown.

---

## /stop

Cierra todos los trades abiertos y apaga el bot completamente.

**Comportamiento:**
- Cierra todas las posiciones abiertas al precio de mercado actual.
- Detiene el loop principal, el hilo de monitoreo y el bot de Telegram.
- El proceso termina. Se debe reiniciar manualmente con `python main.py`.

**Cuando usarlo:** para apagar el bot de forma controlada, especialmente antes de hacer cambios en la configuracion o actualizar el codigo. No usarlo como pausa — para eso esta `/pause`.

---

## /report

Genera un reporte de rendimiento completo bajo demanda.

**Informacion que devuelve:**
- Trades abiertos en este momento.
- Total de trades cerrados.
- Ganadores y perdedores.
- Win rate (porcentaje de trades ganadores).
- Profit factor (ganancia bruta / perdida bruta).
- P&L total acumulado.
- Duracion promedio de los trades.

**Cuando usarlo:** para evaluar el desempeno historico del bot en cualquier momento. El reporte cubre todos los trades registrados en la base de datos desde el inicio.

---

## Notificaciones automaticas

Ademas de los comandos, el bot envia notificaciones de forma automatica en los siguientes eventos:

| Evento | Descripcion |
|---|---|
| Inicio del bot | Confirma que el bot arranco, con el balance inicial. |
| Trade abierto | Par, direccion, precio de entrada, SL, TP, tamaño en lotes y riesgo en USD. |
| Trade cerrado | Par, direccion, precio de entrada y salida, P&L, motivo de cierre y duracion. |
| Pausa por drawdown | Avisa cuando el drawdown supero el limite configurado y el bot se pauso automaticamente. |
| Error en el loop | Si ocurre un error inesperado en el loop principal, el bot se pausa y notifica para que se investigue. |
| Reconexion a MT5 | Si se pierde y se recupera la conexion con MT5. |
| Cierre del bot | Confirma que el bot se apago correctamente. |
| Reporte semanal | Enviado automaticamente segun la config (`reports.weekly_report_day` / `weekly_report_hour` / `timezone`). Con los valores por defecto: domingo a las 20:00 UTC-5 (lunes 01:00 UTC). Mismo contenido que `/report`. |
