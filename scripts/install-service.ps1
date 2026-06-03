<#
.SYNOPSIS
    Registra el bot Drift como una tarea programada de Windows (Task Scheduler).

.DESCRIPTION
    Usa el modulo ScheduledTasks de PowerShell para registrar una tarea llamada "Drift"
    que:
      - Se dispara al iniciar sesion el usuario ASUS (AtLogOn).
      - Corre en la sesion interactiva del usuario logueado — NO en Session 0 (modo
        servicio).  Esto es obligatorio: el paquete MetaTrader5 esta instalado en el
        perfil del usuario y el terminal MT5 corre en la sesion interactiva; un servicio
        en Session 0 no puede conectar con ese terminal.
      - Reinicia el proceso hasta 3 veces (cada 1 minuto) si muere.
      - Tiempo de ejecucion ilimitado (bot de larga duracion).
      - Se puede iniciar con bateria y no se detiene por inactividad.

    No requiere privilegios de administrador para registrar una tarea del usuario
    actual.  Ejecutar desde PowerShell normal (no elevado) es suficiente.

.NOTES
    Despues de correr este script, controla la tarea con:
        Get-ScheduledTask -TaskName Drift          # ver estado
        Start-ScheduledTask -TaskName Drift        # iniciar manualmente
        Stop-ScheduledTask  -TaskName Drift        # detener
        Unregister-ScheduledTask -TaskName Drift   # eliminar la tarea

.PARAMETER PythonExe
    Ruta completa al interprete de Python.  Por defecto usa el interprete de pythoncore
    que tiene instalados MetaTrader5 y python-telegram-bot.

.EXAMPLE
    # Ejecutar desde cualquier directorio (PowerShell normal):
    & "C:\Users\ASUS\code\projects\drift\scripts\install-service.ps1"

.EXAMPLE
    # Especificar un interprete diferente:
    & "C:\Users\ASUS\code\projects\drift\scripts\install-service.ps1" `
        -PythonExe "C:\Users\ASUS\envs\drift\Scripts\python.exe"
#>

[CmdletBinding()]
param(
    [string]$PythonExe = "C:\Users\ASUS\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Resolver rutas
# ---------------------------------------------------------------------------

# La raiz del repo esta un nivel por encima de este script (scripts\).
$RepoRoot = Split-Path -Parent $PSScriptRoot

# Verificar que main.py existe.
$MainPy = Join-Path $RepoRoot "main.py"
if (-not (Test-Path $MainPy)) {
    Write-Error "main.py no encontrado en la ubicacion esperada: $MainPy"
    exit 1
}

# Verificar que el interprete de Python existe.
if (-not (Test-Path $PythonExe)) {
    Write-Error "Interprete de Python no encontrado: $PythonExe`nPasa -PythonExe con la ruta correcta."
    exit 1
}

# Crear el directorio de logs si no existe.
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Force $LogDir | Out-Null
}

$TaskName = "Drift"

Write-Host ""
Write-Host "Registrando tarea programada '$TaskName' via Task Scheduler"
Write-Host "  Python        : $PythonExe"
Write-Host "  Entry point   : $MainPy"
Write-Host "  Working dir   : $RepoRoot"
Write-Host ""

# ---------------------------------------------------------------------------
# Eliminar tarea previa si existe (para reinstalacion limpia).
# ---------------------------------------------------------------------------
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -ne $existing) {
    Write-Host "Tarea '$TaskName' ya existe - eliminando para reinstalar."
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

# ---------------------------------------------------------------------------
# Definir los componentes de la tarea.
# ---------------------------------------------------------------------------

# Accion: ejecutar pythonw.exe (sin consola) main.py con working directory = repo root.
# pythonw.exe corre sin ventana de consola, de modo que el bot queda como proceso
# en segundo plano y no hay ninguna ventana que el usuario pueda cerrar por error.
$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument "main.py" `
    -WorkingDirectory $RepoRoot

# Trigger: al iniciar sesion el usuario ASUS.
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "ASUS"

# Principal: correr como el usuario interactivo logueado.
# RunLevel Limited es suficiente para el bot; la clave es que NO sea
# "run whether user is logged on or not" (eso forzaria Session 0).
$principal = New-ScheduledTaskPrincipal `
    -UserId "ASUS" `
    -LogonType Interactive `
    -RunLevel Limited

# Configuracion: restart en fallo, sin limite de tiempo, sin restriccion de bateria.
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -DontStopOnIdleEnd

# ---------------------------------------------------------------------------
# Registrar la tarea.
# ---------------------------------------------------------------------------
Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Drift forex bot (Daily Lull Scalper). Corre en sesion interactiva del usuario para poder conectar con el terminal MT5." `
    -Force | Out-Null

Write-Host "Tarea '$TaskName' registrada correctamente."
Write-Host ""
Write-Host "IMPORTANTE: el terminal MetaTrader 5 debe estar corriendo y logueado en la"
Write-Host "misma sesion interactiva para que el bot pueda conectarse."
Write-Host ""
Write-Host "Proximos pasos:"
Write-Host "  1. Asegurate de que MT5 este corriendo y logueado."
Write-Host "  2. Inicia la tarea manualmente para verificar:"
Write-Host "       Start-ScheduledTask -TaskName Drift"
Write-Host "  3. Verificar que el bot arrancd mirando el log:"
Write-Host "       Get-Content -Wait logs\drift.log"
Write-Host ""
Write-Host "Comandos de control de la tarea:"
Write-Host "  Get-ScheduledTask -TaskName Drift          # ver estado"
Write-Host "  Start-ScheduledTask -TaskName Drift        # iniciar manualmente"
Write-Host "  Stop-ScheduledTask  -TaskName Drift        # detener"
Write-Host "  Unregister-ScheduledTask -TaskName Drift   # eliminar la tarea"
Write-Host ""
Write-Host "La tarea arrancara automaticamente la proxima vez que inicies sesion en Windows."
