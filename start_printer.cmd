@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Run setup_windows.cmd first.
  exit /b 1
)

rem Optional cloud queue settings. Keep secrets outside Git.
rem set TELEGRAM_RELAY_URL=https://your-relay.example/mcp
rem set TELEGRAM_RELAY_ACCESS_TOKEN=
rem set TELEGRAM_RELAY_WORKER_SECRET=replace-me
rem set TELEGRAM_AUTOPRINT=1

.venv\Scripts\python.exe printer_server.py
