@echo off
REM Idempotent launcher for the DeepSeek jailbreak proxy (cmd variant).
REM Skips starting if the proxy is already healthy on :8790.
REM
REM NOTE: keep this file ASCII-only (cmd.exe reads .cmd in the console
REM OEM codepage; UTF-8 Chinese bytes break the parser).
setlocal

powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 2 http://127.0.0.1:8790/health; if ($r.StatusCode -eq 200) { exit 0 } } catch {}; exit 1" >nul 2>&1
if %ERRORLEVEL%==0 (
  echo proxy already running on :8790
  exit /b 0
)

cd /d "%~dp0"

set "PYEXE="
for %%C in (py.exe python.exe python3.exe) do (
  if not defined PYEXE (
    where %%C >nul 2>nul && set "PYEXE=%%C"
  )
)
if not defined PYEXE (
  echo ERROR: no python launcher found ^(py.exe / python.exe / python3.exe^).
  exit /b 1
)

start "" /b "%PYEXE%" proxy.py --port 8790 --verbose
echo proxy starting on :8790
timeout /t 2 /nobreak >nul
curl -s -o nul -w "health -> HTTP %%{http_code}\n" http://127.0.0.1:8790/health 2>nul
endlocal
