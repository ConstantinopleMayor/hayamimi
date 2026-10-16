@echo off
rem ============================================================
rem  hayamimi portable package - FIRST-RUN SETUP
rem  Double-click this once right after unzipping (optional: the exe also
rem  self-heals pyvenv.cfg on startup; this file adds the dependency check
rem  and clears the download marks).
rem    0) verifies the package is complete (scripts\ .venv\ python\ models\)
rem    1) points .venv at the bundled interpreter (.\python)
rem    2) checks that every runtime dependency imports
rem    3) unblocks files that an unzip tool may have marked as
rem       "downloaded from the internet" (avoids a SmartScreen prompt)
rem  (ASCII only on purpose: cmd reads .bat in its ANSI codepage.)
rem ============================================================
setlocal
for %%I in ("%~dp0.") do set "ROOT=%%~fI"
set "PY=%ROOT%\python\python.exe"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"

echo ============================================
echo   hayamimi - first-run setup
echo   root: %ROOT%
echo ============================================

echo [0/3] checking the package is complete ...
set "INCOMPLETE="
if not exist "%ROOT%\scripts\realtime_transcribe.py" set "INCOMPLETE=%INCOMPLETE% scripts\"
if not exist "%VENV_PY%" set "INCOMPLETE=%INCOMPLETE% .venv\"
if not exist "%PY%" set "INCOMPLETE=%INCOMPLETE% python\"
if not exist "%ROOT%\models" set "INCOMPLETE=%INCOMPLETE% models\"
if defined INCOMPLETE (
    echo.
    echo [X] PACKAGE INCOMPLETE - missing:%INCOMPLETE%
    echo     The subtitle exe cannot work without a fully extracted package:
    echo     unzip the WHOLE archive again ^(never copy just the exe out^),
    echo     then run this file from the new folder.
    echo.
    pause
    exit /b 1
)
echo       OK - scripts\, .venv\, python\, models\ all present

echo [1/3] pointing .venv at the bundled Python ...
> "%ROOT%\.venv\pyvenv.cfg" echo home = %ROOT%\python
>> "%ROOT%\.venv\pyvenv.cfg" echo executable = %ROOT%\python\python.exe
>> "%ROOT%\.venv\pyvenv.cfg" echo include-system-site-packages = false
>> "%ROOT%\.venv\pyvenv.cfg" echo version = 3.12.13

echo [2/3] checking dependencies (a few seconds) ...
"%VENV_PY%" -c "import sys, numpy, soundfile, sounddevice, sherpa_onnx, onnxruntime, ctranslate2, sentencepiece, fugashi, psutil; print('  OK  python', sys.version.split()[0]); print('  OK  prefix', sys.prefix); print('  OK  base  ', sys.base_prefix)"
if errorlevel 1 (
    echo [X] dependency check FAILED - see the messages above.
    pause
    exit /b 1
)
"%VENV_PY%" -c "import soundcard" 2>nul
if errorlevel 1 (echo   [!] soundcard missing - speaker/mix inputs will not work) else (echo   OK  soundcard)
"%VENV_PY%" -c "import kiwipiepy" 2>nul
if errorlevel 1 (echo   [!] kiwipiepy missing - Korean spacing fix disabled) else (echo   OK  kiwipiepy)

echo [3/3] clearing "downloaded from the internet" marks (SmartScreen) ...
powershell -NoProfile -Command "Get-ChildItem -LiteralPath '%ROOT%' -Recurse -File -ErrorAction SilentlyContinue | Unblock-File -ErrorAction SilentlyContinue"
echo       done ^(if this file itself was still blocked, Windows may ask once^)

echo.
echo All set. Now double-click the subtitle exe:
echo   desktop-subtitle\*.exe
echo (or the launcher .bat next to it)
echo ============================================
pause
exit /b 0
