# Comandos de Telegram

## Comandos disponibles

### /status
Muestra el estado actual del bot.
- ¿Está corriendo o pausado?
- Tiempo activo
- Conexión con MT5

### /trades
Lista los trades abiertos actualmente.
- Par, dirección (compra/venta)
- Precio de entrada
- P&L actual
- Stop loss y take profit

### /history
Muestra los últimos trades cerrados.
- Resultado (ganancia/pérdida)
- Motivo de cierre
- Duración

### /balance
Información financiera actual.
- Balance actual
- Rendimiento total ($ y %)
- Drawdown actual

### /pause
Pausa el bot.
- No abre nuevos trades
- Mantiene los trades abiertos (con sus SL/TP)
- El bot sigue monitoreando trades existentes

### /resume
Reanuda el bot después de una pausa.
- Vuelve a buscar señales
- Se usa después de un `/pause` manual o una pausa por drawdown

### /stop
Cierra todo y apaga el bot.
- Cierra todos los trades abiertos al precio actual
- Detiene el bot completamente
- Requiere reiniciar manualmente

### /report
Genera un reporte de rendimiento bajo demanda.
- Trades del período
- Ganadores vs perdedores
- P&L
- Balance actual
- Drawdown

## Notificaciones automáticas

El bot envía notificaciones automáticamente cuando:
- Se abre un trade
- Se cierra un trade (por SL, TP, trailing, o manual)
- El bot se inicia o se apaga
- Ocurre un error
- Se activa la pausa por drawdown
- Reporte semanal (domingos 8pm UTC-5)
