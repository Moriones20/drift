# Getting Started

## Requisitos previos

Antes de instalar Drift, asegurate de tener todo lo siguiente:

- **Windows 10/11** — el paquete `MetaTrader5` de Python solo funciona en Windows.
- **Python 3.10 o superior** — verificar con `python --version`.
- **MetaTrader 5 instalado** — descargarlo desde el sitio de ICMarkets e iniciar sesion con tu cuenta demo (Raw Spread).
- **Bot de Telegram** — creado con @BotFather. Necesitas el token del bot y tu chat ID personal.

---

## Instalacion

### 1. Clonar el repositorio

```bash
git clone <url-del-repositorio>
cd drift
```

### 2. Crear el entorno virtual

```bash
python -m venv .venv
.venv\Scripts\activate
```

### 3. Instalar dependencias

```bash
pip install -r requirements.txt
```

### 4. Configurar config.yaml

Copiar el archivo de ejemplo y completar las credenciales:

```bash
copy config.example.yaml config.yaml
```

Abrir `config.yaml` en un editor de texto y completar los campos obligatorios:

```yaml
broker:
  server: "ICMarketsSC-MT5-Demo"  # Nombre exacto del servidor (File > Open an Account en MT5)
  login: 12345678                  # Numero de cuenta MT5
  password: "tu_password"

telegram:
  bot_token: "123456:ABCdef..."   # Token del bot (de @BotFather)
  chat_id: "tu_chat_id"           # Tu ID personal de Telegram
```

Para obtener el `chat_id`: enviarle cualquier mensaje al bot y luego abrir `https://api.telegram.org/bot<TU_TOKEN>/getUpdates` en el navegador. El campo `"id"` dentro de `"chat"` es tu chat ID.

### 5. Verificar la conexion MT5

Abrir MetaTrader 5 e iniciar sesion en la cuenta demo antes de correr Drift. El terminal de MT5 debe estar abierto y conectado al broker cuando el bot se inicia.

---

## Primer uso

### Iniciar el bot

Con el entorno virtual activado y MT5 abierto:

```bash
python main.py
```

### Que pasa al iniciar

Al arrancar, Drift ejecuta la siguiente secuencia:

1. **Carga la configuracion** desde `config.yaml` y valida todos los campos.
2. **Inicializa la base de datos** SQLite en `drift.db` (se crea automaticamente si no existe).
3. **Conecta con MT5** usando las credenciales del broker. Si falla, el bot se detiene con un error.
4. **Valida los pares configurados** — avisa en el log si algun par no esta disponible en MT5.
5. **Inicia el bot de Telegram** y empieza a escuchar comandos.
6. **Inicia el hilo de monitoreo** (cada 30 segundos por defecto) para actualizar trailing stops y detectar trades cerrados.
7. **Envia notificacion de inicio** a Telegram con el balance actual.
8. **Espera el siguiente cierre de vela H4** — los cierres ocurren a las 00:00, 04:00, 08:00, 12:00, 16:00 y 20:00 UTC.

El mensaje de inicio en Telegram confirma que todo funciona correctamente.

### Verificar que funciona

- El log en consola debe mostrar `Drift bot running — waiting for first H4 candle close`.
- En Telegram debes recibir el mensaje de inicio con el balance de la cuenta.
- El comando `/status` en Telegram debe responder con el estado del bot y la conexion MT5.

---

## Modo demo vs live

Por defecto, `config.yaml` apunta al servidor demo de ICMarkets (`ICMarketsSC-MT5-Demo`). Para operar en cuenta real, cambiar el campo `broker.server` al servidor live correspondiente y usar las credenciales de la cuenta live.

Antes de pasar a live, se recomienda:

1. Correr al menos dos semanas en demo sin errores.
2. Revisar el historial de trades con `/history` y el reporte con `/report`.
3. Verificar que el drawdown nunca supero el 5% en demo.

---

## Archivos y directorios generados

| Archivo/Directorio | Descripcion |
|---|---|
| `config.yaml` | Configuracion principal (no se versiona) |
| `drift.db` | Base de datos SQLite con todo el historial |
| `logs/drift.log` | Log rotativo (hasta 50MB, 5 archivos) |
