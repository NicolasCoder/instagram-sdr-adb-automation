@echo off
chcp 65001 >nul
title Motor Principal de Prospeccao — Olheiro + Sniper ADB
cd /d "%~dp0"

echo ====================================================================
echo   🎯 INICIANDO PROSPECCAO AUTOMATICA NO INSTAGRAM (ADB)
echo ====================================================================
echo   - Celular 1 (Olheiro): Deixe aberto na aba REELS do Instagram
echo   - Celular 2 (Sniper) : Deixe com a tela desbloqueada
echo ====================================================================
echo.

if exist "venv\Scripts\activate.bat" call venv\Scripts\activate.bat

python megazord_prospeccao.py %*
pause
