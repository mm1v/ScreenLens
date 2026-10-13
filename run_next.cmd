@echo off
setlocal
cd /d "%~dp0"
if exist "ScreenLens.Next\bin\Release\net8.0-windows10.0.19041.0\ScreenLens.Next.exe" (
    start "" "ScreenLens.Next\bin\Release\net8.0-windows10.0.19041.0\ScreenLens.Next.exe"
) else (
    "C:\Program Files\dotnet\dotnet.exe" run --project "ScreenLens.Next\ScreenLens.Next.csproj" -c Release
)
