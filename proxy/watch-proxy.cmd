@echo off
rem 每 5 分钟检查破甲代理是否活着，掉了就拉起（幂等）
setlocal
netstat -ano | findstr "127.0.0.1:8790" | findstr "LISTENING" >nul 2>nul
if not errorlevel 1 exit /b 0
start "" /min "C:\Python314\python.exe" "%~dp0proxy.py"
exit /b 0
