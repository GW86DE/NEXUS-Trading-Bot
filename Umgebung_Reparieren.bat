@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title TradingBot - Python Umgebung reparieren

if not exist "venv\Scripts\python.exe" (
    echo Keine venv gefunden. Starte Setup_Einrichtung.bat ...
    call Setup_Einrichtung.bat
    exit /b %errorlevel%
)

echo Aktualisiere pip ...
"venv\Scripts\python.exe" -m pip install --upgrade pip
echo.
echo Installiere/aktualisiere alle benoetigten Pakete ...
"venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo Pruefe requests und Telegram-Setup ...
"venv\Scripts\python.exe" -c "import requests; print('requests OK:', requests.__version__)"
if errorlevel 1 (
    echo FEHLER: requests konnte nicht importiert werden.
    pause
    exit /b 1
)

echo.
echo Umgebung ist repariert.
echo Starte danach Start_Gui.bat.
pause
