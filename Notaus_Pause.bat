@echo off
setlocal
cd /d "%~dp0"
title TradingBot 9.0 NEXUS - NOT-AUS PAUSE
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" notaus_pause.py
) else (
  py -3 notaus_pause.py
)
if errorlevel 1 (
  echo.
  echo FEHLER: Not-Aus konnte nicht gesetzt werden.
  pause
  exit /b 1
)
echo.
echo Der Bot ist jetzt PAUSIERT. Dieses Fenster kann geschlossen werden.
pause
