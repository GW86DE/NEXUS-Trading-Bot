@echo off
cd /d "%~dp0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" settings_migration_gui.py
) else (
  py -3 settings_migration_gui.py
)
