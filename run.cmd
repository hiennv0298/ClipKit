@echo off
rem ClipKit - chay tu ma nguon. Lan dau tu tao moi truong .venv va cai thu vien.
rem Vi du:  run.cmd run "https://www.youtube.com/watch?v=..." -n 5
setlocal
chcp 65001 >nul
set "ROOT=%~dp0"
set "VENV=%ROOT%.venv"

if exist "%VENV%\Scripts\clipkit.exe" goto run

where python >nul 2>nul
if errorlevel 1 (
  echo Khong tim thay Python. Cai Python 3.10 tro len roi chay lai:
  echo     winget install Python.Python.3.12
  exit /b 1
)
echo Lan dau chay: dang tao moi truong va cai thu vien, mat vai phut...
python -m venv "%VENV%" || exit /b 1
"%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install --quiet "%ROOT%." || exit /b 1

:run
"%VENV%\Scripts\clipkit.exe" %*
exit /b %errorlevel%
