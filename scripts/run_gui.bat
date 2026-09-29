@echo off
chcp 65001 >nul
setlocal
call "%~dp0env.bat"
cd /d "%VPC_ROOT%"

if not defined VPC_PYTHON (
  echo [FAIL] Python not found. Run check_env.bat first, or set VPC_PYTHON
  pause
  exit /b 1
)

set PYTHONIOENCODING=utf-8
echo Starting GUI...
"%VPC_PYTHON%" main.py
if errorlevel 1 (
  echo.
  echo Process exited with an error. Run check_env.bat to diagnose.
  pause
  exit /b 1
)
exit /b 0
