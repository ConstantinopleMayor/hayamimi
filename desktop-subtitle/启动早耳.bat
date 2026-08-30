@echo off
rem ============================================================
rem  hayamimi one-click launcher: server + desktop subtitle window
rem  Usage: double-click this file from Explorer.
rem ============================================================
set "PROJECT=D:\AGI\hayamimi"
set "OVERLAY=%PROJECT%\desktop-subtitle"
set "SERVER_URL=http://localhost:8833/"

rem Translation: change the list below to add/remove target languages.
rem   en = FuguMT (ja->en); zh / ko / es ... = M2M-100 (measured for zh/ko).
rem Leave empty (set "TRANSLATE=") to turn translation off.
set "TRANSLATE=--translate zh"

echo ============================================
echo   hayamimi - realtime subtitles
echo ============================================

rem --- 1. hayamimi server (port 8833) ---
>nul 2>&1 curl.exe -s --max-time 3 -o NUL %SERVER_URL%
if errorlevel 1 (
    echo [1/2] Starting hayamimi server...
    pushd "%PROJECT%"
    start "hayamimi server" /min cmd /k "%PROJECT%\.venv\Scripts\python.exe scripts\realtime_transcribe.py --serve 8833 %TRANSLATE%"
    popd
    timeout /t 8 /nobreak >nul
    >nul 2>&1 curl.exe -s --max-time 3 -o NUL %SERVER_URL%
    if errorlevel 1 (
        echo        FAILED to start server. Check the "hayamimi server" window.
    ) else (
        echo        Server ready: http://localhost:8833/  (translation: %TRANSLATE%)
    )
) else (
    echo [1/2] hayamimi server already running
)

rem --- 2. desktop subtitle window ---
echo [2/2] Starting desktop subtitle window...
pushd "%OVERLAY%"
start "" "%OVERLAY%\node_modules\electron\dist\electron.exe" .
popd

echo.
echo Subtitle window controls:
echo   Lock button (lock)  toggle click-through (draggable vs pass-through)
echo   Gear button         open settings menu (font size / font family / quit)
echo   Left-drag           move the window (while lock is ON)
echo   Ctrl+Alt+D          quick toggle click-through
echo   Esc                 quit subtitle window
echo.
echo To stop the server: close the "hayamimi server" console window.
echo ============================================
pause