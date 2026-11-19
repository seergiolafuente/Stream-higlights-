@echo off
chcp 65001 >nul
title Instalar Stream Highlights
cd /d "%~dp0"
echo ============================================
echo   Instalando Stream Highlights
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [!] No tienes Python. Instalando con winget...
  winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
  echo.
  echo Cierra esta ventana y vuelve a ejecutar instalar.bat
  pause
  exit /b
)

where ffmpeg >nul 2>nul
if errorlevel 1 (
  if not exist "bin\ffmpeg.exe" (
    echo [*] Instalando FFmpeg con winget...
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    echo     Si despues sigue sin encontrarse, reinicia el PC o copia ffmpeg.exe y ffprobe.exe en la carpeta "bin".
  )
)

echo [*] Creando entorno de Python...
if not exist ".venv" python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt

echo [*] Librerias de NVIDIA para usar tu grafica en la transcripcion...
pip install nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"

echo.
echo ============================================
echo   Instalado. Abre iniciar.bat para empezar.
echo ============================================
echo   (Opcional) Para usar la IA de Claude, crea la variable de entorno
echo   ANTHROPIC_API_KEY con tu clave de https://console.anthropic.com
pause
