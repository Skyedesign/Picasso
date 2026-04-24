@echo off
REM Picasso — one-click launcher for the imgproc web UI.
REM Double-click this file to start the server and open the UI in your browser.
REM Press Ctrl+C in this window to stop.

setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\imgproc-ui.exe" (
    echo.
    echo imgproc-ui is not installed yet.
    echo.
    echo Run this once to set up:
    echo     python -m venv .venv
    echo     .venv\Scripts\python.exe -m pip install -e .
    echo.
    pause
    exit /b 1
)

echo.
echo  Picasso / imgproc
echo  http://127.0.0.1:8765
echo.
echo  Press Ctrl+C in this window to stop the server.
echo.

REM Kick off the browser in the background after a short delay so the server is ready.
start "" /B cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8765"

REM Foreground: run the server; Ctrl+C here stops it cleanly.
".venv\Scripts\imgproc-ui.exe"

endlocal
