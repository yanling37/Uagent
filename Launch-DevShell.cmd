@echo off
setlocal EnableDelayedExpansion
pushd "%~dp0"
set "ROOT=%CD%"
if exist "toolchains\dotnet\dotnet.exe" (
    set "DOTNET_ROOT=%ROOT%\toolchains\dotnet"
    set "PATH=%DOTNET_ROOT%;%PATH%"
)
for /d %%D in ("toolchains\java\*") do (
    if exist "%%~fD\bin\java.exe" (
        set "JAVA_HOME=%%~fD"
        set "PATH=!JAVA_HOME!\bin;!PATH!"
    )
)
for /d %%D in ("toolchains\node\*") do (
    if exist "%%~fD\node.exe" set "PATH=%%~fD;!PATH!"
)
for /d %%D in ("toolchains\python\*") do (
    if exist "%%~fD\python.exe" set "PATH=%%~fD;!PATH!"
)
for /d %%D in ("toolchains\llvm-mingw\*") do (
    if exist "%%~fD\bin\clang.exe" set "PATH=%%~fD\bin;!PATH!"
)
if exist "toolchains\python\python.exe" set "PATH=%ROOT%\toolchains\python;%PATH%"
if exist "toolchains\git\cmd\git.exe" set "PATH=%ROOT%\toolchains\git\cmd;%PATH%"
if exist "toolchains\utilities\ninja\ninja.exe" set "PATH=%ROOT%\toolchains\utilities\ninja;%PATH%"
if exist "toolchains\utilities\cmake\bin\cmake.exe" set "PATH=%ROOT%\toolchains\utilities\cmake\bin;%PATH%"
if defined LOCALAPPDATA (
    set "CACHE_ROOT=%LOCALAPPDATA%\PortableAI\caches\devshell"
) else (
    set "CACHE_ROOT=%ROOT%\data"
)
set "DOTNET_CLI_HOME=%CACHE_ROOT%\dotnet-home"
set "NUGET_PACKAGES=%CACHE_ROOT%\nuget"
set "npm_config_cache=%CACHE_ROOT%\npm-cache"
set "PIP_CACHE_DIR=%CACHE_ROOT%\pip-cache"
echo Portable DevShell: %ROOT%
cmd /K
popd
