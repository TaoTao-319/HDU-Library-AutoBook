param([Parameter(Mandatory=$true)][ValidateSet('20:00','20:02')][string]$At)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'main.py') --at $At
exit $LASTEXITCODE
