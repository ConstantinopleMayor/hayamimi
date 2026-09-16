@echo off
rem ============================================================
rem  hayamimi one-click launcher: server + desktop subtitle window
rem  Usage: double-click this file from Explorer.
rem  Portable: the project root is derived from THIS file's own
rem  location, so the whole folder can be moved/copied anywhere.
rem  (ASCII only on purpose: cmd reads .bat in its ANSI codepage.)
rem ============================================================
for %%I in ("%~dp0..") do set "PROJECT=%%~fI"
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
rem Default below = OFF. Set it to "--translate api:zh" to instead use the
rem OpenAI-compatible API (requires openai_translate.json in the project
rem root), or "--translate zh" for the local model.
set "TRANSLATE="

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
rem Prefer the packaged exe in dist\win-unpacked (its filename is non-ASCII,
rem hence the wildcard: this file must stay pure ASCII). Falls back to the
rem dev-checkout electron when the packaged build is absent.
set "SUBTITLE_EXE="
for %%F in ("%OVERLAY%\dist\win-unpacked\*.exe") do set "SUBTITLE_EXE=%%~fF"
if defined SUBTITLE_EXE (
    echo [2/2] Starting desktop subtitle window (packaged exe)...
    start "" "%SUBTITLE_EXE%"
) else (
    echo [2/2] Starting desktop subtitle window (dev electron)...
    pushd "%OVERLAY%"
    start "" "%OVERLAY%\node_modules\electron\dist\electron.exe" .
    popd
)

echo.
echo Subtitle window controls:
echo   Pin button          toggle title-bar auto-hide (pinned = always shown)
echo   Lock button         toggle click-through (draggable vs pass-through)
echo   Gear button         open settings menu (font size / font family / quit)
echo   API/Local button    switch translation channel (local MT vs OpenAI API)
echo   Audio button        cycle audio source: mic / speaker loopback / both
echo   Top sliders         subtitle backdrop opacity / window width
echo   Left-drag           move the window (while lock is ON)
echo   Ctrl+Alt+D          quick toggle click-through
echo   Esc                 quit subtitle window
echo.
echo To stop the server: close the "hayamimi server" console window.
echo ============================================
pause
