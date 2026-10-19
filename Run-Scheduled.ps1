param([Parameter(Mandatory=$true)][ValidateSet('21:15','21:20')][string]$At)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'main.py') --at $At
exit $LASTEXITCODE
