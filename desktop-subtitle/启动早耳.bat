@echo off
rem ============================================================
rem  hayamimi one-click launcher: server + desktop subtitle window
rem  Usage: double-click this file from Explorer.
rem  Portable: the project root is derived from THIS file's own
rem  location, so the whole folder can be moved/copied anywhere.
rem
rem  First start loads the ASR models (15-30 s). This launcher
rem  polls port 8833 and opens the window only when the server is
rem  actually up (waits up to ~2 minutes).
rem
rem  While the models load, the server may print:
rem    Invalid model_type: zipformer. Trying to load the model to get its type
rem  That line is NORMAL (sherpa-onnx auto-detects the model type
rem  and keeps loading). It is not an error.
rem
rem  Server output is appended to:
rem    %TEMP%\hayamimi-serve.log / hayamimi-serve.err.log
rem  (the same files the .exe-based launcher writes).
rem
rem  (ASCII only on purpose: cmd reads .bat in its ANSI codepage.)
rem ============================================================
setlocal
for %%I in ("%~dp0..") do set "PROJECT=%%~fI"
set "OVERLAY=%PROJECT%\desktop-subtitle"
set "SERVER_URL=http://localhost:8833/"
set "SRV_LOG=%TEMP%\hayamimi-serve.log"
set "SRV_ERR=%TEMP%\hayamimi-serve.err.log"

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

>nul 2>&1 curl.exe -s --max-time 3 -o NUL %SERVER_URL%
if not errorlevel 1 goto server_ready

echo [1/2] Starting hayamimi server - a console window opens
rem Portable self-heal: when the package carries its own python\, point the
rem venv at it (the same four lines the first-run setup writes), so the
rem server also works on a fresh machine where the original venv path is
rem long gone. The dev checkout has no python\ next to it - never touched.
if not exist "%PROJECT%\python\python.exe" goto no_selfheal
echo        pointing .venv at the bundled python - %PROJECT%\python
> "%PROJECT%\.venv\pyvenv.cfg" echo home = %PROJECT%\python
>> "%PROJECT%\.venv\pyvenv.cfg" echo executable = %PROJECT%\python\python.exe
>> "%PROJECT%\.venv\pyvenv.cfg" echo include-system-site-packages = false
>> "%PROJECT%\.venv\pyvenv.cfg" echo version = 3.12.13
:no_selfheal
pushd "%PROJECT%"
start "hayamimi server" /min cmd /k ""%PROJECT%\.venv\Scripts\python.exe" scripts\realtime_transcribe.py --serve 8833 %TRANSLATE% > "%SRV_LOG%" 2> "%SRV_ERR%""
popd

set /a TRIES=0
:waitloop
>nul 2>&1 curl.exe -s --max-time 3 -o NUL %SERVER_URL%
if not errorlevel 1 goto server_ready
set /a TRIES+=1
if %TRIES% GEQ 40 goto server_failed
if %TRIES% EQU 3 echo        still loading models, first start takes 15-30 seconds
if %TRIES% EQU 10 echo        still loading models, the full model set is large
if %TRIES% EQU 20 echo        still loading models, check the "hayamimi server" window if this never ends
timeout /t 3 /nobreak >nul
goto waitloop

:server_failed
echo.
echo [X] FAILED to start the server after ~2 minutes.
echo     Look at the "hayamimi server" window, and at:
echo       %SRV_LOG%
echo       %SRV_ERR%
echo     The subtitle window was NOT started. Fix the server first,
echo     then run this launcher again.
echo.
pause
exit /b 1

:server_ready
echo [1/2] Server ready: %SERVER_URL%  translation: %TRANSLATE%

rem --- 2. desktop subtitle window ---
rem Look for the packaged exe in TWO shapes (the wildcard is needed because the
rem filename is non-ASCII while this file must stay pure ASCII):
rem   a) desktop-subtitle\*.exe              <- flat packaged layout (portable zip)
rem   b) desktop-subtitle\dist\win-unpacked\*.exe  <- older/nested layout
rem Falls back to the dev checkout's electron when neither exists.
set "SUBTITLE_EXE="
for %%F in ("%OVERLAY%\*.exe") do set "SUBTITLE_EXE=%%~fF"
if defined SUBTITLE_EXE goto win_packaged
for %%F in ("%OVERLAY%\dist\win-unpacked\*.exe") do set "SUBTITLE_EXE=%%~fF"
if defined SUBTITLE_EXE goto win_packaged
goto win_dev

rem NOTE: plain IF/GOTO flow with no parenthesized blocks on purpose: cmd
rem mis-parses parentheses in echo text inside IF ( ) blocks, which broke
rem the earlier version of this launcher.
:win_packaged
echo [2/2] Starting desktop subtitle window - packaged exe
start "" "%SUBTITLE_EXE%"
goto win_done

:win_dev
echo [2/2] Starting desktop subtitle window - dev electron
pushd "%OVERLAY%"
start "" "%OVERLAY%\node_modules\electron\dist\electron.exe" .
popd

:win_done

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
