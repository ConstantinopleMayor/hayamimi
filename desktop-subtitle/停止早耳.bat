@echo off
rem ============================================================
rem  Stop hayamimi: desktop subtitle window + transcribe server
rem  Usage: double-click this file (or right-click -> Run)
rem  (ASCII only on purpose: cmd reads .bat in its ANSI codepage.)
rem ============================================================
echo Stopping hayamimi (desktop subtitle window + server)...

rem --- 1. stop the subtitle window ---
rem Matched by EXECUTABLE PATH, NOT by command line: a command-line match
rem used to hit this batch file's own cmd.exe (its command line contains the
rem folder name desktop-subtitle) and killed this console instantly - the
rem "flashes and closes" bug. cmd.exe / powershell live in System32, so they
rem can never match here. The app processes match one of:
rem   desktop-subtitle / win-unpacked / hayamimi
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -match 'desktop-subtitle|win-unpacked|hayamimi' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem --- 2. stop the hayamimi transcribe server ---
rem Both the venv launcher python and the real python it re-execs have
rem realtime_transcribe in their command line; anything else is left alone.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'realtime_transcribe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

rem --- 3. close a leftover "hayamimi server" console ---
rem The one-click launcher starts the server as: cmd /k ... realtime_transcribe
rem ... -- the python inside is already gone at this point, so close the host
rem console too. Safe: THIS cmd.exe never mentions realtime_transcribe.
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'cmd' -and $_.CommandLine -match 'realtime_transcribe' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"

echo Done. All hayamimi processes stopped.
pause
