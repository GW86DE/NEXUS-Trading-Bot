@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title TradingBot 9.0 NEXUS - Einrichtung

echo ================================================
echo TradingBot 9.0 NEXUS - eToro + OKX EEA Setup
echo ================================================

echo Suche geeignete Python-Version ...
set "PYEXE="

py -3.13 -c "import sys; print(sys.version)" >nul 2>&1
if not errorlevel 1 set "PYEXE=py -3.13"
if not defined PYEXE (
  py -3.12 -c "import sys; print(sys.version)" >nul 2>&1
  if not errorlevel 1 set "PYEXE=py -3.12"
)
if not defined PYEXE (
  py -3.11 -c "import sys; print(sys.version)" >nul 2>&1
  if not errorlevel 1 set "PYEXE=py -3.11"
)
if not defined PYEXE (
  echo Keine Python-3.11/3.12/3.13-Installation gefunden.
  echo Versuche den Standard-Python-Launcher.
  set "PYEXE=py -3"
)

echo Verwende: %PYEXE%
echo.
echo Suche vorhandene Einstellungen/Zustaende aus einer aelteren Version ...
%PYEXE% settings_migration.py --auto
%PYEXE% risk_state_upgrade.py

echo.
if not exist "venv\Scripts\python.exe" (
  echo Erstelle virtuelle Python-Umgebung ...
  %PYEXE% -m venv venv
  if errorlevel 1 (
    echo FEHLER beim Erstellen der virtuellen Umgebung.
    pause
    exit /b 1
  )
) else (
  echo Vorhandene venv wird weiterverwendet.
)

venv\Scripts\python.exe -m pip install --upgrade pip
if exist requirements-lock.txt (
  venv\Scripts\python.exe -m pip install -r requirements-lock.txt
) else (
  venv\Scripts\python.exe -m pip install -r requirements.txt
)
if errorlevel 1 (
  echo FEHLER bei der Installation der Pakete.
  pause
  exit /b 1
)

if exist requirements-test.txt (
  venv\Scripts\python.exe -m pip install -r requirements-test.txt
  if errorlevel 1 (
    echo FEHLER bei der Installation der Testpakete.
    pause
    exit /b 1
  )
)

echo.
echo Migriere lokale Zugangsdaten in sicheren Speicher (Windows DPAPI) ...
venv\Scripts\python.exe credentials_harden.py

echo.
echo Fuehre kompletten NEXUS-9.0-Offline-Volltest aus ...
venv\Scripts\python.exe volltest.py
if errorlevel 1 (
  echo Volltest fehlgeschlagen.
  pause
  exit /b 1
)
echo.
echo ================================================
echo Einrichtung und Offline-Tests erfolgreich.
echo ================================================
echo Starte danach Start_Gui.bat als lokalen Fallback.
echo Fuer die WebUI zuerst ausfuehren: venv\Scripts\python.exe webui_setup.py
echo Vor echtem Handel beide Broker getrennt in PAPER/DEMO pruefen.
pause
