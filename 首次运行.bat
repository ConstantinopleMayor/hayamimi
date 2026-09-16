@echo off
rem ============================================================
rem  hayamimi portable package - FIRST-RUN SETUP
rem  Double-click this ONCE right after unzipping.
rem  It points .venv at the bundled interpreter (.\python) and
rem  verifies that every runtime dependency imports.
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

if not exist "%PY%" (
    echo [X] bundled Python not found: %PY%
    goto :fail
)
if not exist "%VENV_PY%" (
    echo [X] virtual env not found: %VENV_PY%
    goto :fail
)

echo [1/2] pointing .venv at the bundled Python ...
> "%ROOT%\.venv\pyvenv.cfg" echo home = %ROOT%\python
>> "%ROOT%\.venv\pyvenv.cfg" echo executable = %ROOT%\python\python.exe
>> "%ROOT%\.venv\pyvenv.cfg" echo include-system-site-packages = false
>> "%ROOT%\.venv\pyvenv.cfg" echo version = 3.12.13

echo [2/2] checking dependencies (a few seconds) ...
"%VENV_PY%" -c "import sys, numpy, soundfile, sounddevice, sherpa_onnx, onnxruntime, ctranslate2, sentencepiece, fugashi, psutil; print('  OK  python', sys.version.split()[0]); print('  OK  prefix', sys.prefix); print('  OK  base  ', sys.base_prefix)"
if errorlevel 1 (
    echo [X] dependency check FAILED - see the messages above.
    goto :fail
)
"%VENV_PY%" -c "import soundcard" 2>nul
if errorlevel 1 (echo   [!] soundcard missing - speaker/mix inputs will not work) else (echo   OK  soundcard)
"%VENV_PY%" -c "import kiwipiepy" 2>nul
if errorlevel 1 (echo   [!] kiwipiepy missing - Korean spacing fix disabled) else (echo   OK  kiwipiepy)

echo.
echo Done. Now double-click the subtitle exe:
echo   desktop-subtitle\*.exe
echo (or the launcher .bat next to it)
echo ============================================
pause
exit /b 0

:fail
echo.
echo Setup did NOT complete - see the messages above.
pause
exit /b 1
