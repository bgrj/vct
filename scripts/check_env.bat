@echo off
chcp 65001 >nul
setlocal
call "%~dp0env.bat"
cd /d "%VPC_ROOT%"

echo Working directory: %VPC_ROOT%
if not defined VPC_PYTHON (
  echo [FAIL] Python not found. Install Python 3.12, or set VPC_PYTHON to python.exe
  pause
  exit /b 1
)
echo Python: %VPC_PYTHON%
echo.

"%VPC_PYTHON%" -c "import cv2, numpy, PIL, mediapipe; print('deps ok')"
if errorlevel 1 (
  echo.
  echo [FAIL] Dependencies missing. Run:
  echo   "%VPC_PYTHON%" -m pip install -r requirements.txt
  pause
  exit /b 1
)

echo.
"%VPC_PYTHON%" main.py --help
if errorlevel 1 (
  echo [FAIL] main.py --help failed
  pause
  exit /b 1
)

echo.
echo [OK] Environment check passed. Double-click run_gui.bat to start.
pause
exit /b 0
