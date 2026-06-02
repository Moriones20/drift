# Runbook — operar Drift desde Git Bash

Comandos para encender, apagar, monitorear y diagnosticar el bot desde la terminal en Windows con Git Bash.

Todos los comandos asumen que estás parado en la raíz del repo (`C:\Users\ASUS\code\projects\drift`).

## Pre-requisitos

Antes de arrancar el bot, verificá:

1. **MT5 corriendo y logueado** en tu cuenta demo o live. El bot no puede conectar si la terminal está cerrada.
2. **`config.yaml` existe** con tus credenciales reales (broker, telegram).
3. **Las dependencias están instaladas**: `pip install -r requirements.txt`.

## Arrancar el bot (producción — tarea programada de Windows)

Para producción el bot se registra como una tarea de Windows Task Scheduler que se dispara
al iniciar sesión el usuario, corre en la sesión interactiva y reinicia el proceso
automáticamente si muere.

> **Por qué Task Scheduler y no un servicio Windows:** los servicios corren en Session 0,
> una sesión de sistema aislada sin acceso al escritorio del usuario.  El terminal MetaTrader 5
> corre en la sesión interactiva (Session 1) y el paquete `MetaTrader5` de PyPI solo está
> instalado en el perfil del usuario; un servicio en Session 0 no puede conectar con ese
> terminal ni siquiera cargar el módulo.  Task Scheduler con modo "solo cuando el usuario está
> logueado" mantiene el bot en la misma sesión que MT5, resolviendo el problema de raíz.
> Anteriormente se usó WinSW; se migró a Task Scheduler por el aislamiento de Session 0.

### Registrar la tarea (una sola vez)

No se requieren privilegios de administrador.  Desde PowerShell normal, parado en cualquier
directorio:

```powershell
& "C:\Users\ASUS\code\projects\drift\scripts\install-service.ps1"
```

Para especificar un intérprete de Python diferente:

```powershell
& "C:\Users\ASUS\code\projects\drift\scripts\install-service.ps1" `
    -PythonExe "C:\Users\ASUS\envs\drift\Scripts\python.exe"
```

El script elimina cualquier tarea anterior con el mismo nombre y registra la nueva.
La tarea arrancará automáticamente la próxima vez que inicies sesión en Windows.

### Controlar la tarea

```powershell
Get-ScheduledTask -TaskName Drift          # ver estado
Start-ScheduledTask -TaskName Drift        # iniciar manualmente
Stop-ScheduledTask  -TaskName Drift        # detener
Unregister-ScheduledTask -TaskName Drift   # eliminar la tarea
```

### Mecanismo de restart y global error handler

Hay dos capas complementarias de supervivencia:

- **Error handler de Python** (`main.py`): cualquier excepción de Python que escapa todos los
  handlers internos es capturada en el nivel más alto, logueada con traza completa en
  `logs/drift.log`, notificada por Telegram, registrada en la DB, y luego el proceso termina con
  error para que Task Scheduler lo reinicie.
- **Task Scheduler**: reinicia el proceso hasta 3 veces con intervalo de 1 minuto si muere
  por cualquier motivo, incluyendo crashes nativos que Python no puede capturar.

> **Requisito de sesión:** el terminal MetaTrader 5 debe estar corriendo y logueado en la
> misma sesión interactiva.  Si cerrás MT5 y el bot sigue vivo, el reconnect automático
> intentará reconectar; si MT5 no está activo, el bot no podrá operar.

---

## Arrancar el bot (debug / desarrollo manual)

Para debug o desarrollo, podés arrancar sin el servicio.

### Modo foreground

Útil cuando querés ver los logs en vivo en la misma terminal. Bloquea hasta que apagues con Ctrl+C.

```bash
python main.py
```

### Modo background detached (método legacy — NO recomendado para producción)

Este método sobrevive aunque cierres la terminal, pero NO reinicia si el proceso muere.
Usarlo solo para pruebas rápidas; en producción usá la tarea programada (ver sección anterior).

```bash
nohup python main.py > logs/drift.out 2>&1 &
echo $! > .bot.pid
echo "Bot started with PID $(cat .bot.pid)"
```

## Saber si está prendido

### Verificación rápida vía PID file

```bash
if [ -f .bot.pid ] && kill -0 $(cat .bot.pid) 2>/dev/null; then
  echo "Running (PID $(cat .bot.pid))"
else
  echo "Stopped"
fi
```

### Verificación manual

```bash
ps -W | grep python.exe | grep -v grep
```

## Saber dónde está en el ciclo de sesión

¿Está en ventana activa? ¿Cuándo es la próxima sesión?

`_in_session_window` y `_next_session_start` esperan hora del **servidor MT5** (GMT+3), no UTC real. Hay que conectar a MT5 y usar `server_now(get_server_utc_offset(...))`:

```bash
python -c "
from main import _in_session_window, _next_session_start
from drift.config import load_config
from drift.mt5_client import connect, disconnect, get_server_utc_offset, server_now
cfg = load_config()
connect(cfg.broker)
offset = get_server_utc_offset(cfg.pairs[0] if cfg.pairs else 'EURUSD')
now = server_now(offset)
print(f'Server now : {now.strftime(\"%A %Y-%m-%d %H:%M\")} (server time, GMT+3)')
print(f'In window  : {_in_session_window(now, cfg)}')
print(f'Next start : {_next_session_start(now, cfg).strftime(\"%A %Y-%m-%d %H:%M server time\")}')
disconnect()
"
```

## Apagar el bot

### Apagado limpio vía Telegram (preferido)

Mandá `/stop` en el chat del bot. El bot cierra todos los trades abiertos, guarda estado y termina.

### Apagado por PID

Si no podés usar Telegram (por ejemplo, querés matar el proceso porque está colgado):

```bash
if [ -f .bot.pid ]; then
  kill $(cat .bot.pid)
  rm .bot.pid
  echo "Sent SIGTERM"
fi
```

### Apagado forzado (último recurso)

Si el proceso no responde a SIGTERM:

```bash
ps -W | grep python.exe | grep -v grep | awk '{print $1}' | xargs -r kill -9
rm -f .bot.pid
```

## Ver logs

### Tail en vivo

```bash
tail -f logs/drift.log
```

### Últimas 50 líneas

```bash
tail -50 logs/drift.log
```

### Solo errores y warnings recientes

```bash
grep -iE "error|warning|exception" logs/drift.log | tail -20
```

### Buscar evento específico (ej: trade abierto, session close)

```bash
grep "TRADE OPENED" logs/drift.log | tail -5
grep "Session close" logs/drift.log | tail -5
```

## Consultar la base de datos

SQLite en `data/drift.db`. Usamos Python para evitar dependencia del CLI `sqlite3` (no viene por defecto en Git Bash).

### Últimas 10 señales evaluadas

```bash
python -c "
import sqlite3
conn = sqlite3.connect('data/drift.db')
for row in conn.execute('SELECT analyzed_at, pair, decision, reason FROM signals ORDER BY id DESC LIMIT 10'):
    print(f'{row[0][:19]}  {row[1]:<8} {row[2]:<10} {row[3]}')
conn.close()
"
```

### Últimos 5 trades

```bash
python -c "
import sqlite3
conn = sqlite3.connect('data/drift.db')
q = 'SELECT pair, direction, opened_at, closed_at, ROUND(profit_loss, 2), close_reason FROM trades ORDER BY id DESC LIMIT 5'
print(f'{\"PAIR\":<8} {\"DIR\":<5} {\"OPENED\":<19} {\"CLOSED\":<19} {\"PNL\":>8}  REASON')
for row in conn.execute(q):
    pnl = f'\${row[4]:>7.2f}' if row[4] is not None else '   N/A  '
    print(f'{row[0]:<8} {row[1]:<5} {(row[2] or \"\")[:19]:<19} {(row[3] or \"\")[:19]:<19} {pnl}  {row[5] or \"-\"}')
conn.close()
"
```

### Resumen de performance

```bash
python -c "
import sqlite3
conn = sqlite3.connect('data/drift.db')
row = conn.execute('SELECT COUNT(*), SUM(CASE WHEN profit_loss > 0 THEN 1 ELSE 0 END), SUM(CASE WHEN profit_loss < 0 THEN 1 ELSE 0 END), ROUND(SUM(profit_loss), 2), ROUND(AVG(profit_loss), 2) FROM trades WHERE closed_at IS NOT NULL').fetchone()
total, wins, losses, total_pnl, avg_pnl = row
total_pnl = total_pnl or 0.0
avg_pnl = avg_pnl or 0.0
print(f'Total trades  : {total}')
print(f'Wins / Losses : {wins or 0} / {losses or 0}')
print(f'Win rate      : {(wins or 0) / total * 100:.1f}%' if total else 'Win rate      : N/A')
print(f'Total P&L     : \${total_pnl:+.2f}')
print(f'Avg P&L       : \${avg_pnl:+.2f}')
conn.close()
"
```

### Eventos recientes del bot (start, stop, reconexiones, errores)

```bash
python -c "
import sqlite3
conn = sqlite3.connect('data/drift.db')
for row in conn.execute('SELECT event_at, event_type, detail FROM bot_events ORDER BY id DESC LIMIT 10'):
    print(f'{row[0][:19]}  {row[1]:<18} {row[2] or \"\"}')
conn.close()
"
```

## Health checks

### ¿El config parsea bien?

```bash
python -c "from drift.config import load_config; c = load_config(); print(f'OK: {len(c.pairs)} pares, broker={c.broker.server}')"
```

### ¿MT5 responde?

```bash
python -c "
from drift.config import load_config
from drift.mt5_client import connect, disconnect, get_balance
cfg = load_config()
ok = connect(cfg.broker)
print(f'Connected: {ok}')
print(f'Balance: \${get_balance():.2f}' if ok else 'N/A')
disconnect() if ok else None
"
```

### ¿Telegram responde?

```bash
python -c "
import asyncio
from telegram import Bot
from drift.config import load_config
cfg = load_config()
bot = Bot(token=cfg.telegram.bot_token)
me = asyncio.run(bot.get_me())
print(f'Bot @{me.username} OK')
"
```

### ¿Los pip values salen reales (no $10 fallback)?

```bash
python -c "
from drift.config import load_config
from drift.mt5_client import connect, disconnect
import MetaTrader5 as mt5
cfg = load_config()
connect(cfg.broker)
for p in cfg.pairs:
    mt5.symbol_select(p, True)
    info = mt5.symbol_info(p)
    tv = info.trade_tick_value if info else 0
    print(f'{p:<8} \${tv * 10:.2f}/pip/lot')
disconnect()
"
```

Valores esperados aproximados:

| Par | Esperado |
|---|---|
| AUDNZD | $5.93 |
| EURCHF | $12.76 |
| EURJPY | $6.28 |
| GBPJPY | $6.28 |
| EURGBP | $13.44 |

Si alguno sale `$10.00`, no se activó el símbolo — corré el siguiente comando.

## Troubleshooting

### Símbolo no activado en Market Watch

Si en el log de startup ves `pip_value=$10.00/lot` o un warning de `No tick_value for X`, hay que activar los símbolos manualmente:

```bash
python -c "
import MetaTrader5 as mt5
mt5.initialize()
for p in ['AUDNZD', 'EURCHF', 'EURJPY', 'GBPJPY', 'EURGBP']:
    ok = mt5.symbol_select(p, True)
    print(f'{p}: {\"activated\" if ok else \"FAILED\"}')
mt5.shutdown()
"
```

Después reiniciá el bot.

### MT5 desconectado

Si `health_check` falla repetidamente:
1. Abrí MT5 manualmente y verificá que estés logueado.
2. Verificá que `broker.server` en `config.yaml` sea el valor vigente correcto: `ICMarketsSC-Demo`. Si quedó un nombre viejo (ej. `ICMarketsSC-MT5-Demo`), corregilo a `ICMarketsSC-Demo`.
3. Si el broker te desautenticó, volvé a loguear desde la terminal MT5.

### Bot no responde a comandos Telegram

Verificá que el thread de Telegram esté vivo:

```bash
grep "Application started" logs/drift.log | tail -1
```

Si no hay mensaje reciente, mirá si hubo excepciones:

```bash
grep -A 3 "telegram" logs/drift.log | grep -i "error\|exception" | tail -5
```

### Reset completo

Para empezar de cero (perdés la DB y los logs):

```bash
# Parar el bot primero
kill $(cat .bot.pid) 2>/dev/null
sleep 2

# Backup por las dudas
mkdir -p backups
cp data/drift.db backups/drift.db.$(date +%Y%m%d_%H%M%S) 2>/dev/null
cp logs/drift.log backups/drift.log.$(date +%Y%m%d_%H%M%S) 2>/dev/null

# Borrar y reiniciar
rm -f data/drift.db logs/drift.log .bot.pid
python main.py
```

## Atajos útiles

Podés añadir estos aliases a tu `~/.bashrc` o `~/.bash_profile` de Git Bash:

```bash
# Drift control
alias drift-start='nohup python main.py > logs/drift.out 2>&1 & echo $! > .bot.pid'  # solo para debug/dev
alias drift-stop='[ -f .bot.pid ] && kill $(cat .bot.pid) && rm .bot.pid'
alias drift-status='[ -f .bot.pid ] && kill -0 $(cat .bot.pid) 2>/dev/null && echo "Running (PID $(cat .bot.pid))" || echo "Stopped"'
alias drift-log='tail -f logs/drift.log'
alias drift-errors='grep -iE "error|warning|exception" logs/drift.log | tail -20'
```

Después de añadirlos: `source ~/.bashrc`.
