$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ExePath = Join-Path $ScriptDir "ScreenLens.Next\bin\Release\net8.0-windows10.0.19041.0\ScreenLens.Next.exe"

if (Test-Path $ExePath) {
    Start-Process $ExePath
} else {
    & "C:\Program Files\dotnet\dotnet.exe" run --project (Join-Path $ScriptDir "ScreenLens.Next\ScreenLens.Next.csproj") -c Release
}
