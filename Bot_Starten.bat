@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title TradingBot 9.0 NEXUS - eToro + OKX Steuerung

if not exist venv\Scripts\python.exe (
  echo.
  echo Python-Umgebung fehlt. Bitte zuerst Setup_Einrichtung.bat ausfuehren.
  pause
  exit /b 1
)
set "PY=venv\Scripts\python.exe"

:menu
cls
set "PROFIL=ausgewogen"
if exist aktives_profil.txt set /p PROFIL=<aktives_profil.txt
set "MODUS=paper"
if exist handelsmodus.txt set /p MODUS=<handelsmodus.txt

echo ============================================================
echo        TRADINGBOT 9.0 NEXUS - eToro + OKX EEA
echo ============================================================
echo Broker : eToro Aktien + OKX Spot-Krypto
echo eToro  : !MODUS!  ^| OKX-Modus separat in den Einstellungen
echo Profil : !PROFIL!
echo ============================================================
echo  1  GUI starten
echo  2  Trading-Engine starten
echo  3  Handelsmodus waehlen
echo  4  Risiko-Profil waehlen
echo  5  Telegram einrichten
echo  6  Verbindung / Lebensbit testen
echo  7  Depotstatus anzeigen / optional Telegram
echo  8  Intelligence / OpenAI
echo  9  Entscheidungs-Auswertung
echo 10  Volltest ausfuehren
echo 11  eToro Paper-Testorder
echo 12  OKX Diagnose ^(read-only^)
echo 13  WebUI starten
echo 14  OKX LIVE-Arming verwalten
echo  0  Beenden
echo ============================================================
set "wahl="
set /p "wahl=Auswahl: "

if "%wahl%"=="1" goto gui
if "%wahl%"=="2" goto bot
if "%wahl%"=="3" goto modus
if "%wahl%"=="4" goto profil
if "%wahl%"=="5" goto telegram
if "%wahl%"=="6" goto connection
if "%wahl%"=="7" goto depot
if "%wahl%"=="8" goto intelligence
if "%wahl%"=="9" goto decisions
if "%wahl%"=="10" goto volltest
if "%wahl%"=="11" goto paper
if "%wahl%"=="12" goto okxdiag
if "%wahl%"=="13" goto webui
if "%wahl%"=="14" goto okxarm
if "%wahl%"=="0" exit /b 0
goto menu

:gui
%PY% gui_app.py
pause
goto menu
:bot
%PY% nexus_start.py
pause
goto menu
:modus
%PY% handelsmodus.py
pause
goto menu
:profil
%PY% profil_waehlen.py
pause
goto menu
:telegram
%PY% telegram_setup.py
pause
goto menu
:connection
%PY% test_connection.py
pause
goto menu
:depot
%PY% depot_status.py
pause
goto menu
:intelligence
%PY% intelligence_menu.py
pause
goto menu
:decisions
%PY% decision_journal_status.py
pause
goto menu
:volltest
%PY% volltest.py
pause
goto menu
:paper
%PY% paper_test_order.py
pause
goto menu
:okxdiag
%PY% crypto_diagnose.py
pause
goto menu
:webui
%PY% webui_start.py
pause
goto menu
:okxarm
%PY% broker_live_arming.py okx status
echo.
echo Fuer eine 15-minuetige Freigabe: broker_live_arming.py okx arm --minutes 15
pause
goto menu
