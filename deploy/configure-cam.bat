@echo off
REM configure-cam.bat - Doppelklick-Wrapper fuer configure-cam.py
REM
REM Braucht KEINE Admin-Rechte (nur HTTP-Calls gegen VM 155).
REM ASCII-only, keine Em-dashes, Smart-Quotes oder Umlaute.
REM
REM Aufruf:
REM   Doppelklick im Explorer
REM   ODER: configure-cam.bat
REM
REM Optional via Umgebung vorab setzen:
REM   set JARNEX_LOCAL_KEY=abc123...
REM   set JARNEX_DEVICE_ID=bf...
REM   set JARNEX_CAM_ID=1
REM   set JARNEX_API_BASE=http://192.168.10.11:8300/modules/jarnex-admin/api

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo FEHLER: 'python' nicht im PATH gefunden.
    echo Installiere Python 3.11+ oder fuege python.exe zum PATH hinzu.
    pause >nul
    exit /b 1
)

python "%~dp0configure-cam.py" %*

echo.
echo === Skript beendet. Druecke eine Taste zum Schliessen. ===
pause >nul
