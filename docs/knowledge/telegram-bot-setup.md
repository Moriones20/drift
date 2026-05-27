# Telegram Bot — Guía de setup e integración

## Crear el bot

1. Abrir Telegram y buscar `@BotFather`
2. Enviar `/newbot`
3. Darle nombre: "Drift Trading Bot"
4. Darle username: `drift_trading_bot` (debe terminar en `bot`)
5. BotFather te da el **bot token** — guardarlo en `config.yaml`

## Obtener el chat_id

1. Enviar cualquier mensaje al bot
2. Visitar: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Buscar `"chat":{"id": XXXXXXX}` — ese es tu `chat_id`
4. Guardarlo en `config.yaml`

## Librería recomendada

```bash
pip install python-telegram-bot
```

Versión 20+ usa async/await.

## Estructura básica

```python
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

class DriftTelegramBot:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.app = Application.builder().token(token).build()
        self._register_handlers()

    def _register_handlers(self):
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("trades", self.cmd_trades))
        self.app.add_handler(CommandHandler("history", self.cmd_history))
        self.app.add_handler(CommandHandler("balance", self.cmd_balance))
        self.app.add_handler(CommandHandler("pause", self.cmd_pause))
        self.app.add_handler(CommandHandler("resume", self.cmd_resume))
        self.app.add_handler(CommandHandler("stop", self.cmd_stop))
        self.app.add_handler(CommandHandler("report", self.cmd_report))

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Verificar que es el chat autorizado
        if str(update.effective_chat.id) != self.chat_id:
            return
        await update.message.reply_text("Bot status: running...")

    # ... más handlers
```

## Enviar notificaciones (desde fuera del handler)

```python
import asyncio
from telegram import Bot

async def send_notification(token: str, chat_id: str, message: str):
    bot = Bot(token=token)
    await bot.send_message(
        chat_id=chat_id,
        text=message,
        parse_mode="Markdown"
    )
```

## Formato de mensajes

### Trade abierto
```
📈 *TRADE ABIERTO*
Par: EURUSD
Dirección: BUY
Precio: 1.0850
Stop Loss: 1.0730 (-120 pips)
Take Profit: 1.1090 (+240 pips)
Tamaño: 0.01 lots
Riesgo: $5.00 (1.0%)
Hora: 2026-05-26 14:00 (UTC-5)
```

### Trade cerrado
```
✅ *TRADE CERRADO*
Par: EURUSD
Dirección: BUY
Resultado: +$8.50
Motivo: Take Profit
Duración: 2d 6h
Hora: 2026-05-28 20:00 (UTC-5)
```

### Error
```
⚠️ *ERROR*
MT5 desconectado. Reintentando...
Hora: 2026-05-26 14:00 (UTC-5)
```

### Reporte semanal
```
📊 *REPORTE SEMANAL*
Período: 19-25 May 2026

Trades abiertos: 3
Trades cerrados: 5
Ganadores: 3 | Perdedores: 2
P&L semana: +$12.50
P&L acumulado: +$45.00
Balance actual: $545.00
Drawdown actual: 2.1%
Mejor trade: EURUSD +$8.50
Peor trade: GBPUSD -$5.00
```

## Ejecutar el bot en paralelo con el loop de trading

El bot de Telegram necesita correr en su propio thread/async loop. Usar `asyncio` para coordinar con el loop principal de trading.

```python
import asyncio
import threading

# Opción: correr el bot en un thread separado
def run_telegram_bot(bot):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot.app.run_polling()

telegram_thread = threading.Thread(target=run_telegram_bot, args=(bot,), daemon=True)
telegram_thread.start()
```

## Notas

- `parse_mode="Markdown"` permite formateo con `*bold*` y `_italic_`
- Rate limit de Telegram: 30 mensajes por segundo (no es problema para este bot)
- Si el bot no responde a comandos, verificar que el `chat_id` sea correcto
- Para seguridad: siempre verificar `chat_id` en cada handler para que solo tú puedas controlar el bot
