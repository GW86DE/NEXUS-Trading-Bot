@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title TradingBot 9.0 NEXUS - lokale Fallback-GUI

if not exist "venv\Scripts\python.exe" (
    echo.
    echo ============================================================
    echo   Python-Umgebung fehlt
    echo ============================================================
    echo   Bitte zuerst Setup_Einrichtung.bat ausfuehren.
    echo.
    pause
    exit /b 1
)

echo Pruefe Python-Abhaengigkeiten ...
"venv\Scripts\python.exe" -c "import requests, pandas, numpy, sklearn, joblib" >nul 2>&1
if errorlevel 1 (
    echo Fehlende Pakete erkannt. Installiere requirements.txt ...
    if exist requirements-lock.txt (
        "venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
    ) else (
        "venv\Scripts\python.exe" -m pip install -r requirements.txt
    )
    if errorlevel 1 (
        echo.
        echo FEHLER: Pakete konnten nicht installiert werden.
        pause
        exit /b 1
    )
)

"venv\Scripts\python.exe" gui_app.py
if errorlevel 1 (
    echo.
    echo GUI wurde mit einem Fehler beendet.
)
pause
