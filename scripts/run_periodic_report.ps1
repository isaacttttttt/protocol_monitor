param(
    [int]$Hours = 1,
    [switch]$SkipTests
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

& $Python -m app.main report --hours $Hours --send

if (-not $SkipTests) {
    & $Python -m pytest
}
