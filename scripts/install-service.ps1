#Requires -RunAsAdministrator
<#
.SYNOPSIS
    Installs the Drift forex bot as a Windows service managed by NSSM.

.DESCRIPTION
    Uses NSSM (Non-Sucking Service Manager) to register Drift as a Windows service
    named "Drift".  The service:
      - Runs under the current Python interpreter (overridable via -PythonExe).
      - Sets AppDirectory to the repository root so relative paths in main.py work.
      - Restarts automatically on any exit (crash or clean) with a 10-second delay.
      - Applies a throttle so an immediate-crash loop backs off progressively.
      - Redirects stdout and stderr to logs\service-stdout.log / service-stderr.log.
      - Starts automatically on Windows boot (SERVICE_AUTO_START).

.NOTES
    NSSM is NOT included in this repository.  Download nssm.exe from:
        https://nssm.cc/download
    Place nssm.exe somewhere on your PATH (e.g. C:\Windows\System32\) or pass
    its full path via -NssmExe.

    After running this script, start the service with:
        nssm start Drift

    To verify the service is running:
        nssm status Drift

    To stop/restart:
        nssm stop Drift
        nssm restart Drift

    To uninstall the service entirely:
        nssm remove Drift confirm

.PARAMETER PythonExe
    Full path to the Python interpreter.  Defaults to the python.exe found on PATH.

.PARAMETER NssmExe
    Full path to nssm.exe.  Defaults to "nssm" (assumes it is on PATH).

.EXAMPLE
    # Run from an elevated PowerShell prompt in any directory:
    & "C:\Users\ASUS\code\projects\drift\scripts\install-service.ps1"

.EXAMPLE
    # Specify a virtualenv Python and a custom nssm location:
    & ".\scripts\install-service.ps1" `
        -PythonExe "C:\Users\ASUS\envs\drift\Scripts\python.exe" `
        -NssmExe   "C:\tools\nssm\win64\nssm.exe"
#>

[CmdletBinding()]
param(
    [string]$PythonExe = "",
    [string]$NssmExe   = "nssm"
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

# Resolve nssm.
$nssmCmd = Get-Command $NssmExe -ErrorAction SilentlyContinue
if ($null -eq $nssmCmd) {
    Write-Error @"
nssm not found.  Please:
  1. Download nssm.exe from https://nssm.cc/download
  2. Place it on your PATH (e.g. copy to C:\Windows\System32\), OR
  3. Pass its full path: -NssmExe "C:\path\to\nssm.exe"
"@
    exit 1
}

# Log directory (must exist for NSSM output redirection).
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) {
    New-Item -ItemType Directory -Force $LogDir | Out-Null
}

$StdoutLog = Join-Path $LogDir "service-stdout.log"
$StderrLog = Join-Path $LogDir "service-stderr.log"

$ServiceName = "Drift"

Write-Host ""
Write-Host "Installing Drift as a Windows service via NSSM"
Write-Host "  Service name  : $ServiceName"
Write-Host "  Python        : $PythonExe"
Write-Host "  Entry point   : $MainPy"
Write-Host "  Working dir   : $RepoRoot"
Write-Host "  stdout log    : $StdoutLog"
Write-Host "  stderr log    : $StderrLog"
Write-Host ""

# ---------------------------------------------------------------------------
# Remove any previous installation of the same service name.
# ---------------------------------------------------------------------------
$existing = & $NssmExe status $ServiceName 2>$null
if ($LASTEXITCODE -eq 0) {
    Write-Host "Existing service '$ServiceName' found — removing before reinstall."
    & $NssmExe remove $ServiceName confirm
}

# ---------------------------------------------------------------------------
# Install the service.
# ---------------------------------------------------------------------------

# Core: register the executable and the script argument.
& $NssmExe install $ServiceName $PythonExe $MainPy
if ($LASTEXITCODE -ne 0) { Write-Error "nssm install failed"; exit 1 }

# Working directory — ensures relative paths (config.yaml, data/, logs/) resolve correctly.
& $NssmExe set $ServiceName AppDirectory $RepoRoot

# Restart policy: restart on any exit code (including clean exit 0 and crashes).
& $NssmExe set $ServiceName AppExit Default Restart

# Delay between restarts in milliseconds (10 seconds).
& $NssmExe set $ServiceName AppRestartDelay 10000

# Throttle: if the service exits faster than this many milliseconds after starting,
# NSSM considers it a rapid-crash and applies progressive backoff.  Set to 1500 ms
# (if the process dies within 1.5 s of starting it is counted as a rapid crash).
& $NssmExe set $ServiceName AppThrottle 1500

# Redirect stdout and stderr to log files.  RotateBytes = 10 MB, RotateOnline = 1
# means NSSM rotates the log while the service is running, matching the Python
# RotatingFileHandler already in place for drift.log.
& $NssmExe set $ServiceName AppStdout         $StdoutLog
& $NssmExe set $ServiceName AppStderr         $StderrLog
& $NssmExe set $ServiceName AppStdoutCreationDisposition 4
& $NssmExe set $ServiceName AppStderrCreationDisposition 4
& $NssmExe set $ServiceName AppRotateFiles    1
& $NssmExe set $ServiceName AppRotateBytes    10485760

# Automatic start on boot.
& $NssmExe set $ServiceName Start SERVICE_AUTO_START

# Human-readable description visible in services.msc.
& $NssmExe set $ServiceName Description "Drift autonomous forex bot (Asian Session Scalper)"

Write-Host ""
Write-Host "Service '$ServiceName' installed successfully."
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Make sure MT5 is running and logged in."
Write-Host "  2. Start the service:   nssm start $ServiceName"
Write-Host "  3. Check status:        nssm status $ServiceName"
Write-Host "  4. Watch the log:       Get-Content -Wait logs\service-stdout.log"
Write-Host ""
Write-Host "NOTE: NSSM restarts the process after a crash (Python-level or native)."
Write-Host "      The global error handler in main.py writes a traceback to logs\drift.log"
Write-Host "      before exiting, so every Python-level crash leaves evidence."
