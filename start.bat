@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
  py -3 ESS_WaveTest.pyz gui
) else (
  where python >nul 2>nul
  if errorlevel 1 (
    echo Python 3.10+ is required. Install Python, then run this file again.
    pause
    exit /b 2
  )
  python ESS_WaveTest.pyz gui
)
if errorlevel 1 pause
