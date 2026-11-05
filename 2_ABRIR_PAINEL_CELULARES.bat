@echo off
chcp 65001 >nul
title Painel Multi-Celular Scrcpy (Olheiro + Sniper)
cd /d "%~dp0"

echo ====================================================================
echo   📱 ABRINDO PAINEL MULTI-CELULAR (SCRCPY)
echo ====================================================================

if exist "venv\Scripts\activate.bat" call venv\Scripts\activate.bat

python -c "import os, subprocess, shutil; from pathlib import Path; from dotenv import load_dotenv; load_dotenv('.env'); scrcpy = os.getenv('SCRCPY_PATH','').strip() or shutil.which('scrcpy') or 'scrcpy'; adb = os.getenv('ADB_PATH','').strip() or shutil.which('adb') or 'adb'; olh = os.getenv('SERIAL_OLHEIRO','').strip(); snp = os.getenv('SERIAL_SNIPER','').strip(); out = subprocess.run([adb, 'devices'], capture_output=True, text=True).stdout.splitlines(); devs = [l.split()[0] for l in out if len(l.split())>=2 and l.split()[1]=='device']; olh = olh or (devs[0] if devs else ''); snp = snp or (devs[1] if len(devs)>=2 else ''); print(f'Conectados: {devs}'); subprocess.Popen([scrcpy, '-s', olh, '--no-audio', '--window-title', 'OLHEIRO (Cacador de Leads)']) if olh else print('Nenhum celular detectado!'); subprocess.Popen([scrcpy, '-s', snp, '--no-audio', '--window-title', 'SNIPER (Fechador de Contratos)']) if (snp and snp != olh) else None"

timeout /t 3 >nul
