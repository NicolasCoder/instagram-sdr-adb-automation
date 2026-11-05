@echo off
chcp 65001 >nul
title Instagram SDR & ADB Command Center (:4950)
cd /d "%~dp0"

echo ====================================================================
echo   🚀 INICIANDO INSTAGRAM SDR ^& ADB COMMAND CENTER (:4950)...
echo ====================================================================

if exist "venv\Scripts\activate.bat" call venv\Scripts\activate.bat

start "" "http://127.0.0.1:4950"
python sdr_web_dashboard.py
pause
