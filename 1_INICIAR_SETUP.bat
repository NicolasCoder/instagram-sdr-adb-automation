@echo off
chcp 65001 >nul
color 0A
title [1/5] Setup Automatico — Instagram SDR & ADB Prospector
cd /d "%~dp0"

echo ====================================================================
echo   🚀 SETUP AUTOMATICO — INSTAGRAM SDR ^& ADB PROSPECTOR
echo ====================================================================
echo.

:: 1. Verifica Python
echo [1/5] Verificando instalacao do Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [!] Python nao encontrado! Instalando Python 3.11 via winget...
    winget install Python.Python.3.11 --silent --accept-package-agreements --accept-source-agreements
    echo [OK] Python instalado! Feche esta janela e abra novamente o script.
    pause
    exit /b
)
echo [OK] Python detectado.
echo.

:: 2. Cria arquivo .env se nao existir
if not exist ".env" (
    echo [2/5] Criando arquivo .env a partir do .env.example...
    copy /Y ".env.example" ".env" >nul
    echo [OK] Arquivo .env criado! Edite o .env para colocar seus dados opcionais.
) else (
    echo [2/5] Arquivo .env ja existe.
)
echo.

:: 3. Cria e ativa venv + instala requirements.txt
echo [3/5] Preparando ambiente virtual Python e instalando dependencias...
if not exist "venv" (
    python -m venv venv
)
call venv\Scripts\activate
pip install --upgrade pip --quiet
pip install -r requirements.txt
echo [OK] Dependencias Python instaladas.
echo.

:: 4. Baixa modelos de visao computacional (OpenCV Gender + YOLOv11n)
echo [4/5] Verificando modelos de Visao Computacional (YOLOv11n + OpenCV)...
python models\download_models.py
echo.

:: 5. Inicializa uiautomator2 nos celulares conectados via USB
echo [5/5] Verificando celulares Android conectados no cabo USB (ADB)...
echo ATENCAO: Desbloqueie os celulares e aceite o pop-up "Permitir Depuracao USB".
adb devices
python -m uiautomator2 init
adb shell svc power stayon usb >nul 2>&1

echo.
echo ====================================================================
echo   ✅ SETUP CONCLUIDO COM SUCESSO!
echo   - Para espelhar as telas no PC: 2_ABRIR_PAINEL_CELULARES.bat
echo   - Para iniciar a prospeccao  : 3_INICIAR_PROSPECCAO.bat
echo   - Para abrir o CRM Web (:4950): 4_ABRIR_CRM_DASHBOARD.bat
echo ====================================================================
pause
