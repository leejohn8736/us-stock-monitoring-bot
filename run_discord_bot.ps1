$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$envFile = Join-Path $PSScriptRoot ".env"
if (-not (Test-Path $envFile)) {
    throw ".env 파일이 없습니다. Telegram과 Alpaca 설정을 먼저 입력하세요."
}

Get-Content -LiteralPath $envFile -Encoding UTF8 | ForEach-Object {
    $line = $_.Trim()
    if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
        return
    }
    $parts = $line.Split("=", 2)
    $name = $parts[0].Trim()
    $value = $parts[1].Trim()
    if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
        $value = $value.Substring(1, $value.Length - 2)
    }
    [Environment]::SetEnvironmentVariable($name, $value, "Process")
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "가상환경이 없습니다. install_windows.ps1을 먼저 실행하세요."
}

# 텔레그램 봇 구동 진입점 실행 (필요에 따라 모듈명이나 파일 경로를 맞춰서 사용하세요)
& $python (Join-Path $PSScriptRoot "main.py")
exit $LASTEXITCODE
