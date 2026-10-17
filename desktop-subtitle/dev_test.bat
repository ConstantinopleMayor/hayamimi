@echo off
rem dev test: run the subtitle window from source (main.js edits apply immediately)
cd /d "%~dp0"
start "" "node_modules\electron\dist\electron.exe" .
