@echo off
setlocal
cd /d "%~dp0"

where dotnet >nul 2>nul
if errorlevel 1 (
    echo [ERROR] .NET 8 SDK was not found. Install the .NET 8 Desktop Runtime and SDK.
    pause
    exit /b 1
)

tasklist /fi "imagename eq LinovelibDesktop.exe" /nh | find /i "LinovelibDesktop.exe" >nul
if not errorlevel 1 (
    echo [INFO] The WPF UI is already running.
    powershell -NoProfile -Command "$shell = New-Object -ComObject WScript.Shell; $null = $shell.AppActivate('轻小说 EPUB 下载器')" >nul 2>nul
    exit /b 0
)

dotnet build "wpf\LinovelibDesktop\LinovelibDesktop.csproj" --nologo --verbosity:minimal
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] The WPF application could not be built. Exit code %EXIT_CODE%.
    pause
    exit /b %EXIT_CODE%
)

start "" "wpf\LinovelibDesktop\bin\Debug\net8.0-windows\LinovelibDesktop.exe"
exit /b 0
