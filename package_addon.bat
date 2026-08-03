@echo off
REM Double-click wrapper: builds the Blender addon zip and syncs to AppData.
REM Pass extra args after the script name, e.g. package_addon.bat -OutZip ".\dist\custom.zip"
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0package_addon.ps1" -SyncAppData %*
echo.
pause
