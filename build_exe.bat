@echo off
setlocal

cd /d "%~dp0"

set "APP_NAME=Warcraft3TriggerInjector"
set "ENTRY_FILE=main.py"
set "BUILD_DIR=build\pyinstaller"
set "DIST_DIR=dist"
set "PY_CMD="
set "PROJECT_ROOT=%CD%"

if not exist "%ENTRY_FILE%" (
    echo [ERROR] Entry file not found: %ENTRY_FILE%
    exit /b 1
)

if defined VIRTUAL_ENV (
    set "PY_CMD=python"
) else (
    where py >nul 2>nul && set "PY_CMD=py"
)

if not defined PY_CMD (
    set "PY_CMD=python"
)

echo Using interpreter launcher: %PY_CMD%

%PY_CMD% -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --name "%APP_NAME%" ^
  --distpath "%DIST_DIR%" ^
  --workpath "%BUILD_DIR%\work" ^
  --specpath "%BUILD_DIR%\spec" ^
  --hidden-import PySide6.QtCore ^
  --hidden-import PySide6.QtGui ^
  --hidden-import PySide6.QtWidgets ^
  --hidden-import shiboken6 ^
  --add-data "%PROJECT_ROOT%\sample_patch.json;." ^
  "%ENTRY_FILE%"

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

echo.
echo Build complete:
echo   %CD%\%DIST_DIR%\%APP_NAME%\%APP_NAME%.exe

endlocal
