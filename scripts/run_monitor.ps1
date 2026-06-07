param(
    [switch]$RunOnce
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

Set-Location $ProjectRoot

if (-not (Test-Path -LiteralPath $Python)) {
    $SystemPython = Get-Command python -ErrorAction SilentlyContinue
    if (-not $SystemPython) {
        throw "Python is not available and .venv was not found."
    }
    & $SystemPython.Source -m venv .venv
    & $Python -m pip install -r requirements.txt
}

if ($RunOnce) {
    & $Python -m app.main --run-once
} else {
    & $Python -m app.main
}
