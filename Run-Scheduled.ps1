param([Parameter(Mandatory=$true)][ValidateSet('20:00','20:02')][string]$At)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONIOENCODING = 'utf-8'
$bookingArgs = @('--at', $At)
if ($At -eq '20:00') { $bookingArgs += @('--backup-at', '20:02') }
& (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') (Join-Path $PSScriptRoot 'main.py') @bookingArgs
exit $LASTEXITCODE
