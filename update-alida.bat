@echo off
setlocal enabledelayedexpansion

REM ===========================================================================
REM  Picasso  -  Manual Update (zip-drop fallback)
REM
REM  Use this when the in-app updater can't reach GitHub or you want to
REM  apply a pre-release build. The in-app updater (banner inside Picasso)
REM  is the normal path.
REM
REM  How to use:
REM    1. Extract the new Picasso zip somewhere (Downloads is fine).
REM    2. Run THIS update-alida.bat from inside the extracted folder.
REM    3. It auto-detects your existing install location, stops the running
REM       picasso.exe, copies the new files in, and preserves your batches/,
REM       source/, and imgproc.yaml.
REM ===========================================================================

set "SRC=%~dp0"
if "%SRC:~-1%"=="\" set "SRC=%SRC:~0,-1%"

REM ---- Locate the existing install ---------------------------------------
REM Priority: install-path marker (written by install.bat) → common
REM locations → ask the user. install.bat writes the marker on every run,
REM so once Picasso is installed anywhere, this works without prompts.

set "DST="
set "MARKER=%LOCALAPPDATA%\Picasso\install-path.txt"
if exist "%MARKER%" (
    for /f "usebackq delims=" %%L in ("%MARKER%") do set "DST=%%L"
)

if not defined DST (
    for %%P in (
        "C:\Picasso"
        "C:\Codebase\Picasso"
        "%USERPROFILE%\Picasso"
        "%USERPROFILE%\Documents\Picasso"
    ) do (
        if exist "%%~P\picasso.exe" if not defined DST set "DST=%%~P"
    )
)

if not defined DST (
    echo.
    echo  Could not auto-detect your Picasso install.
    set /p "DST=  Enter the path (e.g. C:\Picasso): "
)

REM Trim quotes/whitespace.
set "DST=%DST:"=%"
for /f "tokens=* delims= " %%A in ("%DST%") do set "DST=%%A"

echo.
echo  ====================================================
echo      PICASSO  -  MANUAL UPDATE
echo  ====================================================
echo.
echo  From: %SRC%
echo  To:   %DST%
echo.
echo  Your batches/, source/, imgproc.yaml are preserved.
echo.

if not exist "%DST%\picasso.exe" (
    echo  [ERROR] No existing picasso.exe found at %DST%.
    echo  Run install.bat from the extracted folder if this is a fresh setup.
    echo.
    pause & exit /b 1
)
pause

REM ---- Stop running instance, robocopy, refresh marker -------------------
echo.
echo  Stopping running picasso.exe ...
taskkill /F /IM picasso.exe >nul 2>&1
ping -n 2 127.0.0.1 >nul

echo  Copying new files ...
robocopy "%SRC%" "%DST%" /E ^
    /XD batches source ^
    /XF imgproc.yaml update-alida.bat install-path.txt ^
    /NFL /NDL /NP

REM Refresh the marker (catches the case where she moved the install dir
REM since last install.bat run).
if not exist "%LOCALAPPDATA%\Picasso" mkdir "%LOCALAPPDATA%\Picasso"
> "%LOCALAPPDATA%\Picasso\install-path.txt" echo %DST%

echo.
echo  ====================================================
echo      UPDATE COMPLETE
echo  ====================================================
echo.
echo  Launch Picasso from your desktop shortcut to start the new version.
echo.
pause
endlocal
