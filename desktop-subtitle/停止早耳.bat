@echo off
rem ============================================================
rem  Stop hayamimi: desktop subtitle window + transcribe server
rem  Usage: double-click this file (or: stop_hayamimi.bat)
rem ============================================================
echo Stopping hayamimi (desktop subtitle window + server)...

rem --- 1. stop the electron subtitle window ---
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'electron.exe' -and $_.CommandLine -match 'desktop-subtitle' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem --- 2. stop the hayamimi transcribe server ---
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'realtime_transcribe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Done. All hayamimi processes stopped.
pause