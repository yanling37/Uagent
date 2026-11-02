@echo off
setlocal EnableExtensions
pushd "%~dp0"
rem Build-only: this script does not install software or access package indexes.
set "BUILD_PYTHON="
if exist ".build-venv\Scripts\python.exe" set "BUILD_PYTHON=.build-venv\Scripts\python.exe"
if not defined BUILD_PYTHON (
  where py >nul 2>nul
  if errorlevel 1 goto :missing
  set "BUILD_PYTHON=py -3"
)
%BUILD_PYTHON% -c "import sys, tkinter, sqlite3, PyInstaller; assert sys.platform == 'win32', 'Build on Windows'; assert sys.version_info >= (3,10), 'Python 3.10+ required'"
if errorlevel 1 goto :missing
%BUILD_PYTHON% -m unittest discover -s tests -v
if errorlevel 1 goto :failed
%BUILD_PYTHON% -m PyInstaller --noconfirm --clean --onefile --windowed --name PortableAI --distpath . --workpath .build-work --specpath .build-spec app.py
if errorlevel 1 goto :failed
if not exist "PortableAI.exe" goto :failed
echo PortableAI.exe was produced. Target users do not need Python installed.
echo This does NOT verify native launch or live API connectivity. Run the Windows acceptance checklist.
popd
exit /b 0
:missing
echo BUILD prerequisites missing: Windows, Python 3.10+ with Tk and SQLite, PyInstaller 6.x.
echo No packages were installed. After reviewing, run Setup-Windows-Build.cmd to opt in.
popd
exit /b 1
:failed
echo Build or tests failed. Do not distribute any old PortableAI.exe as this build.
popd
exit /b 1
