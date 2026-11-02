@echo off
setlocal EnableDelayedExpansion
pushd "%~dp0"
if exist "PortableAI.exe" (
    start "" "%CD%\PortableAI.exe"
    popd
    exit /b 0
)
if exist "toolchains\python\python.exe" (
    "toolchains\python\python.exe" app.py
    popd
    exit /b !errorlevel!
)
set "USB_PYTHON="
for /R "%CD%\toolchains\python" %%F in (python.exe) do if not defined USB_PYTHON set "USB_PYTHON=%%F"
if defined USB_PYTHON (
    "%USB_PYTHON%" app.py
    set "RC=!errorlevel!"
    popd
    exit /b !RC!
)
where py >nul 2>nul
if not errorlevel 1 (
    py -3 app.py
    popd
    exit /b !errorlevel!
)
where python >nul 2>nul
if not errorlevel 1 (
    python app.py
    popd
    exit /b !errorlevel!
)
echo Python runtime not found. Build PortableAI.exe on a Windows development PC,
echo or unpack a portable Python runtime into toolchains\python.
pause
popd
exit /b 1
