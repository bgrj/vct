@echo off
REM Shared env for video-pics-cut scripts. Sets VPC_PYTHON if unset.
REM Override: set VPC_PYTHON=C:\Path\to\python.exe

set "VPC_ROOT=%~dp0.."
pushd "%VPC_ROOT%" >nul
set "VPC_ROOT=%CD%"
popd >nul

if defined VPC_PYTHON (
  if exist "%VPC_PYTHON%" goto :eof
)

where python >nul 2>&1
if %ERRORLEVEL%==0 (
  for /f "delims=" %%i in ('where python') do (
    set "VPC_PYTHON=%%i"
    goto :eof
  )
)

where py >nul 2>&1
if %ERRORLEVEL%==0 (
  for /f "delims=" %%i in ('py -3.12 -c "import sys; print(sys.executable)" 2^>nul') do (
    set "VPC_PYTHON=%%i"
    goto :eof
  )
  for /f "delims=" %%i in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
    set "VPC_PYTHON=%%i"
    goto :eof
  )
)

set "VPC_PYTHON="
goto :eof
