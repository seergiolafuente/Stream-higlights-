@echo off
chcp 65001 >nul
title Stream Highlights
cd /d "%~dp0"
rem Arrastra el video del directo encima de este archivo (y si quieres, tambien el chat .json)
if "%~1"=="" (
  echo Arrastra el video del directo encima de procesar.bat
  pause
  exit /b
)
set CHAT=
if not "%~2"=="" set CHAT=--chat "%~2"
call .venv\Scripts\activate.bat
python -m src.main "%~1" %CHAT%
echo.
pause
