@echo off
REM nextdns-allowlist.bat - Doppelklick-Wrapper fuer nextdns-allowlist.py
REM
REM Braucht KEINE Admin-Rechte (nur HTTPS-Calls gegen api.nextdns.io).
REM ASCII-only, keine Em-dashes oder Smart-Quotes.
REM
REM Aufruf:
REM   Doppelklick im Explorer (nutzt Default-Domains tuyaeu.com + tuyaus.com)
REM   ODER: nextdns-allowlist.bat <domain1> <domain2> ...
REM
REM Optional vorab setzen:
REM   set NEXTDNS_API_KEY=...
REM   set NEXTDNS_PROFILE_ID=41835f
REM   set NEXTDNS_DOMAINS=tuyaeu.com,tuyaus.com,custom.example

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo FEHLER: 'python' nicht im PATH gefunden.
    echo Installiere Python 3.11+ oder fuege python.exe zum PATH hinzu.
    pause >nul
    exit /b 1
)

python "%~dp0nextdns-allowlist.py" %*

echo.
echo === Skript beendet. Druecke eine Taste zum Schliessen. ===
pause >nul
