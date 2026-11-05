@echo off
chcp 65001 >nul
title Reset de Conexao USB & Servidor ADB
cd /d "%~dp0"

echo ====================================================================
echo   🔄 REINICIANDO SERVIDOR ADB ^& ESCANEANDO PORTAS USB
echo ====================================================================
echo.
pnputil /scan-devices >nul 2>&1
adb kill-server
adb start-server
echo.
echo Dispositivos ADB conectados:
adb devices -l
echo.
pause
