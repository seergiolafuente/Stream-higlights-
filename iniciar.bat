@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Primero ejecuta instalar.bat
  pause
  exit /b
)
start "" ".venv\Scripts\pythonw.exe" -m src.gui
