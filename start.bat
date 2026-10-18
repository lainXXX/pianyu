@echo off
rem Double-click to start the local web service and open it in the browser.
rem Messages are ASCII on purpose: cmd.exe reads .bat files in the console code page (GBK).
cd /d "%~dp0"
set PYTHONUTF8=1
set "PATH=%PATH%;%LOCALAPPDATA%\Microsoft\WinGet\Links"
if not exist ".venv\Scripts\python.exe" (
  echo [vtx] .venv\Scripts\python.exe not found. Run this file from the project folder.
  pause
  exit /b 1
)
start "" http://127.0.0.1:8765
echo [vtx] Server starting at http://127.0.0.1:8765  - close this window to stop it.
".venv\Scripts\python.exe" -m vtx.server
echo.
echo [vtx] Server stopped. See the messages above.
pause
