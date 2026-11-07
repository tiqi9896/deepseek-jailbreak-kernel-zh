@echo off
REM ============================================================
REM  Watchdog for the DeepSeek jailbreak proxy.
REM  Checks 127.0.0.1:8790 every run; restarts the proxy if it is down.
REM  Idempotent, returns immediately.
REM
REM  NOTE: keep this file ASCII-only. cmd.exe reads .cmd in the
REM  console OEM codepage; UTF-8 Chinese bytes get mangled and can
REM  break the parser outright.
REM ============================================================
setlocal
netstat -ano | findstr "127.0.0.1:8790" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 exit /b 0
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
start "" /min %PYEXE% "%~dp0proxy.py" --port 8790 --verbose
exit /b 0
