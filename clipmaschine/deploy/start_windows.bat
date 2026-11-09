@echo off
REM Clipmaschine fuer Windows: startet die Maschine und nach einem Absturz automatisch neu.
REM Autostart: Win+R -> shell:startup -> Verknuepfung zu dieser Datei hineinlegen.
chcp 65001 >nul
cd /d "%~dp0..\.."
set PY=python
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe

:loop
%PY% -m clipmaschine run
set CODE=%ERRORLEVEL%
if "%CODE%"=="2" goto ende
if "%CODE%"=="3" goto ende
echo.
echo Clipmaschine wurde beendet (Code %CODE%) - Neustart in 30 Sekunden ... (Strg+C zum Abbrechen)
timeout /t 30 /nobreak >nul
goto loop

:ende
echo Konfigurationsfehler oder Maschine laeuft bereits - kein automatischer Neustart.
pause
