@echo off
setlocal
cd /d "%~dp0"
venv\Scripts\python.exe crypto_diagnose.py
pause
