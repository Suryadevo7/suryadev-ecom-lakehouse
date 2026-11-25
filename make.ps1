<#
.SYNOPSIS
  PowerShell equivalent of the Makefile for Windows users without GNU make.
.EXAMPLE
  .\make.ps1 all
  .\make.ps1 generate -Rows 500
#>
param(
    [ValidateSet("help", "setup", "lint", "generate", "run", "test", "docs", "all", "clean", "distclean")]
    [string]$Target = "help",
    [int]$Rows = 10000,
    [int]$Seed = 42
)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$Venv = ".venv"
$Bin  = if ($IsLinux -or $IsMacOS) { "$Venv/bin" } else { "$Venv/Scripts" }
$Py   = Join-Path $Bin "python"
$Dbt  = Join-Path $Bin "dbt"
$env:DBT_VERSION_CHECK = "false"

function Invoke-Step([scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "command failed with exit code $LASTEXITCODE" }
}

function Setup {
    $stamp = Join-Path $Venv ".installed"
    if ((Test-Path $stamp) -and ((Get-Item $stamp).LastWriteTime -ge (Get-Item "requirements.txt").LastWriteTime)) { return }
    $sysPy = if (Get-Command python3 -ErrorAction SilentlyContinue) { "python3" } else { "python" }
    Invoke-Step { & $sysPy -m venv $Venv }
    Invoke-Step { & $Py -m pip install --quiet --upgrade pip }
    Invoke-Step { & $Py -m pip install --quiet -r requirements.txt }
    New-Item -ItemType File -Force $stamp | Out-Null
}

function Lint {
    Setup
    $Ruff = Join-Path $Bin "ruff"
    Invoke-Step { & $Ruff check . }
    Invoke-Step { & $Ruff format --check . }
}

function Generate { Setup; Invoke-Step { & $Py src/generate.py --rows $Rows --seed $Seed --out data/bronze } }

function Run-Dbt([string]$Command) {
    Push-Location dbt
    try { Invoke-Step { & (Join-Path ".." $Dbt) $Command --profiles-dir . } } finally { Pop-Location }
}

function Run   { Setup; Run-Dbt "run" }
function Test  {
    Setup; Run-Dbt "test"
    $env:RECONCILE_STRICT = "1"
    try { Invoke-Step { & $Py -m pytest -v } } finally { Remove-Item Env:RECONCILE_STRICT }
}
function Docs {
    Setup
    Push-Location dbt
    try { Invoke-Step { & (Join-Path ".." $Dbt) docs generate --static --profiles-dir . } } finally { Pop-Location }
    "open dbt\target\static_index.html in a browser"
}
function Clean {
    foreach ($p in "data", "dbt/target", "dbt/logs", "dbt/.user.yml", "logs", ".pytest_cache") {
        if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
}

switch ($Target) {
    "help"      { "targets: setup lint generate run test docs all clean distclean   (-Rows $Rows -Seed $Seed)" }
    "setup"     { Setup }
    "lint"      { Lint }
    "generate"  { Generate }
    "run"       { Run }
    "test"      { Test }
    "docs"      { Docs }
    "all"       { Setup; Lint; Generate; Run; Test }
    "clean"     { Clean }
    "distclean" { Clean; if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv } }
}
