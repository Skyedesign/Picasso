@echo off
setlocal enabledelayedexpansion

REM ===========================================================================
REM  Picasso  -  Installer
REM  Run once on each machine. No Administrator required.
REM
REM  What this does:
REM    1. Creates required directories (batches, source)
REM    2. Creates a Desktop shortcut to picasso.exe
REM    3. Creates a Start Menu shortcut
REM    4. Creates a Startup shortcut (auto-launch on Windows login)
REM    5. Starts Picasso immediately - browser opens automatically
REM ===========================================================================

REM Resolve this script's directory (works when invoked from anywhere).
set "PICASSO=%~dp0"
if "%PICASSO:~-1%"=="\" set "PICASSO=%PICASSO:~0,-1%"

set "EXE=%PICASSO%\picasso.exe"
set "DESKTOP=%USERPROFILE%\Desktop"
set "STARTMENU=%APPDATA%\Microsoft\Windows\Start Menu\Programs"
set "STARTUP=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"

echo.
echo  ====================================================
echo      PICASSO  -  INSTALLER
echo  ====================================================
echo.
echo  Install path : %PICASSO%
echo.

REM ---- Pre-flight check ----------------------------------------------------
if not exist "%EXE%" (
    echo  [ERROR] picasso.exe not found at:
    echo          %EXE%
    echo.
    echo  Make sure the full Picasso folder was extracted before running this.
    echo.
    pause & exit /b 1
)

REM ---- 1. Working directories ---------------------------------------------
echo  [1/4] Creating directories...
if not exist "%PICASSO%\batches" mkdir "%PICASSO%\batches"
if not exist "%PICASSO%\source"  mkdir "%PICASSO%\source"
REM Record install location so update-alida.bat can find it on any machine,
REM regardless of where the zip was extracted (C:\Picasso, C:\Codebase\Picasso,
REM Downloads\..., etc.). The in-app updater doesn't need this — it uses
REM sys.executable.parent — but the manual fallback bat does.
if not exist "%LOCALAPPDATA%\Picasso" mkdir "%LOCALAPPDATA%\Picasso"
> "%LOCALAPPDATA%\Picasso\install-path.txt" echo %PICASSO%
echo         OK

REM ---- 2. Desktop shortcut -------------------------------------------------
echo  [2/4] Creating Desktop shortcut...
powershell -NoProfile -Command ^
  "try { $ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut('%DESKTOP%\Picasso.lnk'); $s.TargetPath='%EXE%'; $s.WorkingDirectory='%PICASSO%'; $s.Description='Picasso (imgproc) - product image resizer'; $s.Save(); Write-Host '         Desktop shortcut created.' } catch { Write-Host ('         WARNING: ' + $_.Exception.Message) }"

REM ---- 3. Start Menu shortcut ---------------------------------------------
echo  [3/4] Creating Start Menu shortcut...
powershell -NoProfile -Command ^
  "try { $ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut('%STARTMENU%\Picasso.lnk'); $s.TargetPath='%EXE%'; $s.WorkingDirectory='%PICASSO%'; $s.Description='Picasso (imgproc) - product image resizer'; $s.Save(); Write-Host '         Start Menu shortcut created.' } catch { Write-Host ('         WARNING: ' + $_.Exception.Message) }"

REM ---- 4. Startup shortcut (auto-launch with Windows) ---------------------
REM  Picasso is a tiny local server. Auto-starting it means the desktop
REM  shortcut just opens the browser to the running instance via the
REM  single-instance probe instead of cold-starting each time.
echo  [4/4] Creating Startup shortcut (auto-launch on login)...
powershell -NoProfile -Command ^
  "try { $ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut('%STARTUP%\Picasso.lnk'); $s.TargetPath='%EXE%'; $s.WorkingDirectory='%PICASSO%'; $s.WindowStyle=7; $s.Description='Picasso (imgproc) - launched at login'; $s.Save(); Write-Host '         Startup shortcut created.' } catch { Write-Host ('         WARNING: ' + $_.Exception.Message) }"

echo.
echo  ====================================================
echo      INSTALL COMPLETE
echo  ====================================================
echo.
echo  Starting Picasso now - your browser will open shortly.
echo  The Picasso console window can stay open in the background;
echo  press Ctrl+C in it to stop the server.
echo.

start "" "%EXE%"

REM Don't pause; the user will be looking at the browser tab.
endlocal
