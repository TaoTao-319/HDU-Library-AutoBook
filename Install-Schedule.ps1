param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$taskNames = @('HDU-Library-2000', 'HDU-Library-2002')
$legacyTaskNames = @('HDU-Library-2003', 'HDU-Library-2110', 'HDU-Library-2115', 'HDU-Library-2120', 'HDU-Library-2140', 'HDU-Library-2145')
if ($Remove) {
    foreach ($taskName in ($taskNames + $legacyTaskNames)) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    Write-Output 'HDU booking tasks removed.'
    exit 0
}
if ((Get-TimeZone).Id -ne 'China Standard Time') {
    throw 'Set the Windows time zone to China Standard Time before installing these tasks.'
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'))) {
    throw 'Create .venv and install requirements.txt first.'
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot '.local\credentials.xml'))) {
    throw 'Run Save-Credential.ps1 first.'
}
$windowsUser = [Security.Principal.WindowsIdentity]::GetCurrent().Name
# Interactive logon lets DPAPI decrypt the password without storing the Windows password.
$principal = New-ScheduledTaskPrincipal -UserId $windowsUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 8)
$schedule = @(
    @{Name='HDU-Library-2000'; Prepare='19:55'; Submit='20:00'},
    @{Name='HDU-Library-2002'; Prepare='20:01'; Submit='20:02'}
)
foreach ($entry in $schedule) {
    $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + `
        (Join-Path $PSScriptRoot 'Run-Scheduled.ps1') + '" -At ' + $entry.Submit
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $entry.Prepare
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description ('HDU library: pre-login at ' + $entry.Prepare + ', submit at ' + $entry.Submit + ' Asia/Shanghai; skips disabled weekdays.')
    Register-ScheduledTask -TaskName $entry.Name -InputObject $task -Force | Out-Null
    Enable-ScheduledTask -TaskName $entry.Name | Out-Null
}
foreach ($taskName in $legacyTaskNames) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}
Get-ScheduledTask -TaskName $taskNames | Select-Object TaskName,State
Write-Output 'ENABLED: prepare at 19:55, submit at 20:00; reuse session for 20:02 on failure. Independent 20:01 watchdog retained.'
Write-Output 'Enable the desired booking weekdays in user_config.yml. Logs are stored in logs/.'
