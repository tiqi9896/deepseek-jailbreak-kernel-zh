# ============================================================
#  DeepSeek jailbreak kernel - installer (Windows PowerShell)
#
#  . copies the plugin into $HERMES_HOME\plugins\deepseek-jailbreak-core
#  . runs the plugin self-test  (22 assertions)
#  . runs the proxy self-test   (125 checks, real HTTP against a local stub)
#  . FAILS LOUDLY if either self-test does not pass
#
#  Usage:
#    powershell -ExecutionPolicy Bypass -File install.ps1
#    powershell -ExecutionPolicy Bypass -File install.ps1 -WithProxy
#    powershell -ExecutionPolicy Bypass -File install.ps1 -HermesHome C:\path\to\hermes
#    powershell -ExecutionPolicy Bypass -File install.ps1 -CheckOnly
#
#  NOTE: this file is UTF-8 WITH BOM on purpose.
#        Windows PowerShell 5.1 reads .ps1 files in the local ANSI codepage
#        unless a BOM is present - without it, non-ASCII characters get
#        mangled and the parser breaks outright ("ParserError").
# ============================================================

[CmdletBinding()]
param(
    [string]$HermesHome = $env:HERMES_HOME,
    [switch]$WithProxy,
    [switch]$CheckOnly,
    [int]$Port = 8790
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path

# ---- locate a python interpreter (never hard-code a path) ----
$Py = $null
foreach ($cand in @("py.exe", "python.exe", "python3.exe")) {
    $cmd = Get-Command $cand -ErrorAction SilentlyContinue
    if ($cmd) { $Py = $cmd.Source; break }
}
if (-not $Py) { $Py = "python" }

Write-Host "python: $Py"
try {
    Write-Host ("version: " + (& $Py --version 2>&1))
} catch {
    Write-Host "WARNING: could not run python --version" -ForegroundColor Yellow
}

# ---- locate the Hermes home ----
if (-not $HermesHome) {
    $cands = @(
        (Join-Path $HOME ".hermes"),
        (Join-Path $HOME "hermes"),
        (Join-Path $env:LOCALAPPDATA "hermes"),
        (Join-Path $env:APPDATA "hermes")
    )
    foreach ($c in $cands) {
        if ($c -and (Test-Path (Join-Path $c "config.yaml"))) { $HermesHome = $c; break }
    }
}
if (-not $HermesHome -or -not (Test-Path $HermesHome)) {
    Write-Host "ERROR: Hermes home not found." -ForegroundColor Red
    Write-Host "       Pass it explicitly:  -HermesHome C:\path\to\hermes"
    Write-Host "       (a directory containing config.yaml)"
    exit 1
}
Write-Host "HERMES_HOME: $HermesHome"

$PluginDir = Join-Path (Join-Path $HermesHome "plugins") "deepseek-jailbreak-core"

# ---- install the plugin ----
if (-not $CheckOnly) {
    $pluginsRoot = Join-Path $HermesHome "plugins"
    if (-not (Test-Path $pluginsRoot)) { New-Item -ItemType Directory -Path $pluginsRoot | Out-Null }
    if (Test-Path $PluginDir) {
        $bak = "$PluginDir.bak.$(Get-Date -Format yyyyMMddHHmmss)"
        Write-Host "existing plugin found - backing it up to $bak"
        Move-Item $PluginDir $bak
    }
    Copy-Item (Join-Path $Here "plugin") $PluginDir -Recurse
    Write-Host "installed plugin -> $PluginDir"
} else {
    Write-Host "-CheckOnly: not installing"
}

# ---- self-test 1: the plugin (offline) ----
Write-Host ""
Write-Host "=== self-test 1/2: plugin (offline) ==="
$RC1 = 1
$st = Join-Path $PluginDir "selftest.py"
if (Test-Path $st) {
    & $Py $st
    $RC1 = $LASTEXITCODE
} else {
    Write-Host "SKIP: plugin selftest not found at $st" -ForegroundColor Yellow
}

# ---- self-test 2: the proxy (real HTTP, local stub upstream) ----
Write-Host ""
Write-Host "=== self-test 2/2: proxy (real HTTP against a local stub) ==="
& $Py (Join-Path $Here "proxy\proxy.py") --self-test
$RC2 = $LASTEXITCODE

# ---- verdict ----
Write-Host ""
if ($RC1 -eq 0 -and $RC2 -eq 0) {
    Write-Host "INSTALL OK - both self-tests passed." -ForegroundColor Green
} else {
    Write-Host "INSTALL FAILED - plugin selftest rc=$RC1, proxy selftest rc=$RC2" -ForegroundColor Red
    Write-Host "Do not report success. Fix the failures above first."
    exit 1
}

# ---- optional: start the proxy ----
if ($WithProxy) {
    Write-Host ""
    Write-Host "=== starting the proxy on :$Port ===" -ForegroundColor Cyan
    if (-not $env:DEEPSEEK_API_KEY) {
        Write-Host "WARNING: DEEPSEEK_API_KEY is not set in this shell." -ForegroundColor Yellow
        Write-Host "         The proxy will still start, but upstream calls will 401."
    }
    $log = Join-Path $Here "proxy\proxy.log"
    Start-Process -FilePath $Py `
        -ArgumentList @((Join-Path $Here "proxy\proxy.py"), "--port", "$Port", "--verbose") `
        -WorkingDirectory (Join-Path $Here "proxy") `
        -RedirectStandardOutput $log -RedirectStandardError "$log.err" `
        -WindowStyle Hidden
    Start-Sleep -Seconds 2
    try {
        $h = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:$Port/health"
        Write-Host ("health -> HTTP " + $h.StatusCode)
    } catch {
        Write-Host "health check failed: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    Write-Host "Next:  python tests\proxy_smoke.py --port $Port"
}

Write-Host ""
Write-Host "Next steps"
Write-Host "----------"
Write-Host "1. Reload the plugin without restarting existing sessions:"
Write-Host "     hermes plugins disable deepseek-jailbreak-core"
Write-Host "     hermes plugins enable deepseek-jailbreak-core"
Write-Host "2. Verify coverage:"
Write-Host "     python tools\audit-deepseek-jailbreak.py -v"
Write-Host "3. Read the allowlist section in AGENTS.md."
Write-Host "   The default admits every deepseek* model id; deepseek-v4-pro is excluded."
exit 0
