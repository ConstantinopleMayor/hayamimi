@echo off
rem ============================================================
rem  hayamimi one-click launcher: server + desktop subtitle window
rem  Usage: double-click this file from Explorer.
rem ============================================================
set "PROJECT=D:\AGI\hayamimi"
set "OVERLAY=%PROJECT%\desktop-subtitle"
set "SERVER_URL=http://localhost:8833/"

rem Translation targets (comma-separated, mixed freely):
rem   zh / en / ko / es ... = local models (en = FuguMT ja->en;
rem     others = M2M-100; zh/ko measured, see docs/TRANSLATE_M2M.md)
rem   api:zh / api:en,ko / api = OpenAI-compatible API translation
rem     of the DETECTED source language (any language, not just ja).
rem     Requires a usable openai_translate.json in the project root
rem     (copy openai_translate.example.json and fill base_url/model).
rem     The subtitle window's API/Local button also switches channels.
rem Leave empty (set "TRANSLATE=") to turn translation off.
rem Default below = API zh (uses openai_translate.json). If you have no
rem API config, change it to "--translate zh" for the local model.
set "TRANSLATE=--translate api:zh"

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
echo   API/Local button    switch translation channel (local MT vs OpenAI API)
echo   Left-drag           move the window (while lock is ON)
echo   Ctrl+Alt+D          quick toggle click-through
echo   Esc                 quit subtitle window
echo.
echo To stop the server: close the "hayamimi server" console window.
echo ============================================
pause