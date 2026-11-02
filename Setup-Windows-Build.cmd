@echo off
setlocal EnableExtensions
pushd "%~dp0"
echo This optional BUILD-machine setup downloads PyInstaller and dependencies from your configured pip index.
echo It creates .build-venv locally. It does not change Windows VPN or require administrator rights.
choice /C YN /N /M "Authorize download and installation in .build-venv? [Y/N] "
if errorlevel 2 goto :cancel
where py >nul 2>nul
if errorlevel 1 goto :failed
if not exist ".build-venv\Scripts\python.exe" py -3 -m venv .build-venv
if errorlevel 1 goto :failed
".build-venv\Scripts\python.exe" -m pip install "pyinstaller>=6,<7"
if errorlevel 1 goto :failed
echo Setup complete. Run Build-Windows.cmd next.
popd
exit /b 0
:cancel
echo Cancelled. Nothing was installed.
popd
exit /b 2
:failed
echo Setup failed. Check Python installation and network access on the build computer.
popd
exit /b 1
