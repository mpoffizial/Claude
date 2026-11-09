@echo off
REM ================================================================
REM  Clipmaschine fuer Windows - einfach doppelklicken.
REM  Erster Start: richtet alles ein (Python-Umgebung, Pakete,
REM  Einrichtungs-Assistent inkl. TikTok-Verbindung).
REM  Danach: startet die Maschine und nach einem Absturz automatisch neu.
REM  Autostart: Win+R -> shell:startup -> Verknuepfung zu dieser Datei.
REM ================================================================
chcp 65001 >nul
cd /d "%~dp0..\.."
title Clipmaschine

where ffmpeg >nul 2>nul
if errorlevel 1 goto no_ffmpeg

if exist ".venv\Scripts\python.exe" goto have_venv
echo Erster Start: richte die Python-Umgebung ein ...
py -3 -m venv .venv >nul 2>nul
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\Scripts\python.exe" goto no_python
:have_venv
set PY=.venv\Scripts\python.exe

echo Pruefe Pakete ...
%PY% -m pip install --disable-pip-version-check -q -r clipmaschine\requirements.txt
if errorlevel 1 goto pip_failed
REM yt-dlp aktuell halten - Twitch aendert seine Seite oefter
%PY% -m pip install --disable-pip-version-check -q --upgrade yt-dlp >nul 2>nul

set CHROME_FOUND=0
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set CHROME_FOUND=1
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set CHROME_FOUND=1
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set CHROME_FOUND=1
if "%CHROME_FOUND%"=="0" %PY% -m playwright install chromium

if exist "config.yaml" goto loop
%PY% -m clipmaschine setup --no-start
if errorlevel 1 goto ende

:loop
%PY% -m clipmaschine run
set CODE=%ERRORLEVEL%
if "%CODE%"=="2" goto ende
if "%CODE%"=="3" goto ende
echo.
echo Clipmaschine wurde beendet (Code %CODE%) - Neustart in 30 Sekunden ... (Strg+C zum Abbrechen)
timeout /t 30 /nobreak >nul
goto loop

:no_ffmpeg
echo ffmpeg fehlt. Installieren mit:   winget install Gyan.FFmpeg
echo Danach dieses Fenster schliessen und die Datei erneut doppelklicken.
goto ende

:no_python
echo Python 3.10 oder neuer fehlt. Installieren mit:   winget install Python.Python.3.12
echo Danach die Datei erneut doppelklicken.
goto ende

:pip_failed
echo Die Pakete konnten nicht installiert werden (Internetverbindung pruefen).
goto ende

:ende
echo.
pause
