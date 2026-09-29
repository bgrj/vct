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

set "EPISODE_DIR=%~1"
if "%EPISODE_DIR%"=="" (
  echo Usage: drag an episode folder onto this script, or:
  echo   run_episode.bat "path\your-show\01"
  echo.
  set /p EPISODE_DIR=Paste episode folder path:
)

if "%EPISODE_DIR%"=="" (
  echo No input directory. Exiting.
  pause
  exit /b 1
)

set "EPISODE_DIR=%EPISODE_DIR:"=%"

if not exist "%EPISODE_DIR%" (
  echo [FAIL] Directory not found: %EPISODE_DIR%
  pause
  exit /b 1
)

echo.
echo Default: sample first 3 cues. Type all for full episode.
set /p MODE=Press Enter for 3-cue sample, or type all:
set "EXTRA=--max-cues 3"
if /i "%MODE%"=="all" set "EXTRA="

set PYTHONIOENCODING=utf-8
echo.
echo Processing: %EPISODE_DIR%
echo Log: %VPC_ROOT%\_run.log
echo.

"%VPC_PYTHON%" -u main.py --input "%EPISODE_DIR%" %EXTRA% > "%VPC_ROOT%\_run.log" 2>&1
set "RC=%ERRORLEVEL%"

echo.
echo Exit code: %RC%
echo Check *-pics\_report.txt under the episode folder, and _run.log in the tool root.
pause
exit /b %RC%
