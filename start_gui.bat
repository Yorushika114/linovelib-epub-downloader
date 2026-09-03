@echo off
setlocal
cd /d "%~dp0"

where dotnet >nul 2>nul
if errorlevel 1 (
    echo [ERROR] .NET 8 SDK was not found. Install the .NET 8 Desktop Runtime and SDK.
    pause
    exit /b 1
)

dotnet run --project "wpf\LinovelibDesktop\LinovelibDesktop.csproj"
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" (
    echo [ERROR] The GUI exited with code %EXIT_CODE%.
    pause
)
exit /b %EXIT_CODE%
