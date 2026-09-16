@echo off
rem ============================================================
rem  Stop hayamimi: desktop subtitle window + transcribe server
rem  Usage: double-click this file (or right-click -> Run)
rem  (ASCII only on purpose: cmd reads .bat in its ANSI codepage.)
rem ============================================================
echo Stopping hayamimi (desktop subtitle window + server)...

rem --- 1. stop the subtitle window ---
rem Matched by command line ONLY: a packaged build runs the renamed app exe
rem (NOT 'electron.exe'), while the dev checkout runs electron.exe. Both have
rem 'desktop-subtitle' in their command line; the server does not, so nothing
rem is killed twice.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'desktop-subtitle' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem --- 2. stop the hayamimi transcribe server ---
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'realtime_transcribe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Done. All hayamimi processes stopped.
pause
