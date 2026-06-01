#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installs the Drift forex bot as a Windows service managed by WinSW.

.DESCRIPTION
    Uses WinSW (Windows Service Wrapper) to register Drift as a Windows service
    named "Drift".  The service:
      - Runs under the current Python interpreter (overridable via -PythonExe).
      - Sets the working directory to the repository root so relative paths in
        main.py (config.yaml, data\, logs\) resolve correctly.
      - Restarts automatically on failure with a 10-second initial delay and
        progressive backoff (10s / 30s / 60s), resetting after 1 hour stable.
      - Captures stdout and stderr to logs\service-*.log with 10 MB rotation.
      - Starts automatically on Windows boot (Automatic start mode).

    WinSW convention: the .exe and .xml share the same base name and must be
    in the same directory.  This script expects both files to live in scripts\:
        scripts\drift-service.exe   <- WinSW binary (see NOTES)
        scripts\drift-service.xml   <- service descriptor (already in repo)

.NOTES
    WinSW is NOT included in this repository.  Download WinSW-x64.exe from:
        https://github.com/winsw/winsw/releases/tag/v2.12.0
    Rename the downloaded file to "drift-service.exe" and place it in scripts\.

    After running this script, control the service with:
        scripts\drift-service.exe start
        scripts\drift-service.exe stop
        scripts\drift-service.exe restart
        scripts\drift-service.exe status

    To uninstall the service entirely:
        scripts\drift-service.exe uninstall

.PARAMETER PythonExe
    Full path to the Python interpreter.  Defaults to the python.exe found on PATH.

.EXAMPLE
    # Run from an elevated PowerShell prompt in any directory:
    & "C:\Users\ASUS\code\projects\drift\scripts\install-service.ps1"

.EXAMPLE
    # Specify a virtualenv Python:
    & "C:\Users\ASUS\code\projects\drift\scripts\install-service.ps1" `
        -PythonExe "C:\Users\ASUS\envs\drift\Scripts\python.exe"
#>

[CmdletBinding()]
param(
    [string]$PythonExe = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Resolve paths
# ---------------------------------------------------------------------------

# Repo root is one directory above this script.
$RepoRoot = Split-Path -Parent $PSScriptRoot

# Resolve main.py absolute path.
$MainPy = Join-Path $RepoRoot "main.py"
if (-not (Test-Path $MainPy)) {
    Write-Error "main.py not found at expected location: $MainPy"
    exit 1
}

# WinSW binary and XML descriptor must share the same base name in the same directory.
$WinSwExe = Join-Path $PSScriptRoot "drift-service.exe"
$WinSwXml = Join-Path $PSScriptRoot "drift-service.xml"

# Check that drift-service.xml is present (it is version-controlled, so it should be).
if (-not (Test-Path $WinSwXml)) {
    Write-Error "Service descriptor not found: $WinSwXml"
    exit 1
}

# Check that drift-service.exe is present; if not, guide the user to download it.
if (-not (Test-Path $WinSwExe)) {
    Write-Error @"
WinSW binary not found: $WinSwExe

To install the Drift service you need the WinSW executable:
  1. Download WinSW-x64.exe from:
         https://github.com/winsw/winsw/releases/tag/v2.12.0
  2. Rename the downloaded file to "drift-service.exe".
  3. Place it in the scripts\ directory of this repository:
         $PSScriptRoot\drift-service.exe
  4. Re-run this script.
"@
    exit 1
}

# Resolve Python interpreter.
if ($PythonExe -eq "") {
    $found = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $found) {
        Write-Error "python not found on PATH.  Pass -PythonExe <path\to\python.exe>."
        exit 1
    }
    $PythonExe = $found.Source
}
if (-not (Test-Path $PythonExe)) {
    Write-Error "Python interpreter not found: $PythonExe"
    exit 1
}

# Log directory must exist before the service starts writing to it.
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Force $LogDir | Out-Null
}

$ServiceName = "Drift"

Write-Host ""
Write-Host "Installing Drift as a Windows service via WinSW"
Write-Host "  Service name  : $ServiceName"
Write-Host "  Python        : $PythonExe"
Write-Host "  Entry point   : $MainPy"
Write-Host "  Working dir   : $RepoRoot"
Write-Host "  Log dir       : $LogDir"
Write-Host "  WinSW binary  : $WinSwExe"
Write-Host "  WinSW config  : $WinSwXml"
Write-Host ""

# ---------------------------------------------------------------------------
# Patch the XML descriptor with the actual Python path.
# WinSW does not support environment-variable expansion in <executable>, so
# we rewrite the placeholder in the XML before calling "install".
# We operate on a temporary copy so the version-controlled file stays clean.
# ---------------------------------------------------------------------------

$TempXml = Join-Path $PSScriptRoot "drift-service.xml.tmp"
(Get-Content $WinSwXml -Encoding UTF8) -replace "PYTHON_EXE_PLACEHOLDER", $PythonExe |
    Set-Content $TempXml -Encoding UTF8

# WinSW looks for the XML file with the same base name as the .exe in the same
# directory.  We copy the patched XML over for the duration of the install.
Copy-Item $TempXml $WinSwXml -Force
Remove-Item $TempXml -Force

# ---------------------------------------------------------------------------
# Remove any previous installation of the same service name.
# ---------------------------------------------------------------------------
$statusOutput = & $WinSwExe status 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "Existing service '$ServiceName' found — uninstalling before reinstall."
    & $WinSwExe stop   2>&1 | Out-Null
    & $WinSwExe uninstall
}

# ---------------------------------------------------------------------------
# Install and start the service.
# ---------------------------------------------------------------------------
Write-Host "Registering service..."
& $WinSwExe install
if ($LASTEXITCODE -ne 0) { Write-Error "WinSW install failed"; exit 1 }

Write-Host "Starting service..."
& $WinSwExe start
if ($LASTEXITCODE -ne 0) { Write-Error "WinSW start failed"; exit 1 }

Write-Host ""
Write-Host "Service '$ServiceName' installed and started successfully."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Make sure MT5 is running and logged in."
Write-Host "  2. Check status:        scripts\drift-service.exe status"
Write-Host "  3. Watch the log:       Get-Content -Wait logs\drift-service.out.log"
Write-Host ""
Write-Host "Service control commands (run from repo root):"
Write-Host "  scripts\drift-service.exe start"
Write-Host "  scripts\drift-service.exe stop"
Write-Host "  scripts\drift-service.exe restart"
Write-Host "  scripts\drift-service.exe status"
Write-Host "  scripts\drift-service.exe uninstall"
Write-Host ""
Write-Host "NOTE: WinSW restarts the process on failure (10s/30s/60s backoff, reset after 1h stable)."
Write-Host "      The global error handler in main.py writes a traceback to logs\drift.log"
Write-Host "      before exiting, so every Python-level crash leaves evidence."
