@echo off
REM handover-key.bat - Doppelklick-Wrapper fuer handover-key.py
REM
REM Schreibt einen Credential (z.B. NextDNS-API-Key) in eine ACL-geschuetzte
REM File unter %USERPROFILE%\.credentials\<name>.key, damit Claude per
REM Subprocess-ENV-Substitution darauf zugreifen kann OHNE dass der Key
REM im Chat-Tool-Result-stdout auftaucht.
REM
REM ASCII-only, keine Em-dashes oder Smart-Quotes.
REM Braucht KEINE Admin-Rechte.
REM
REM Aufruf:
REM   Doppelklick im Explorer (nutzt name=nextdns-api)
REM   ODER: handover-key.bat <name>
REM   ODER: handover-key.bat <name> <key>   (NICHT empfohlen: Shell-History)

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo FEHLER: 'python' nicht im PATH gefunden.
    echo Installiere Python 3.11+ oder fuege python.exe zum PATH hinzu.
    pause >nul
    exit /b 1
)

python "%~dp0handover-key.py" %*

echo.
echo === Skript beendet. Druecke eine Taste zum Schliessen. ===
pause >nul
