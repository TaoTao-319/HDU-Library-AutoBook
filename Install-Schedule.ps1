param([switch]$Remove)
$ErrorActionPreference = 'Stop'
$taskNames = @('HDU-Library-2140', 'HDU-Library-2145')
$legacyTaskNames = @('HDU-Library-2000', 'HDU-Library-2002', 'HDU-Library-2003', 'HDU-Library-2110', 'HDU-Library-2115', 'HDU-Library-2120')
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
    -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 2 -Seconds 30)
$schedule = @(
    @{Name='HDU-Library-2140'; Prepare='21:39'; Submit='21:40'},
    @{Name='HDU-Library-2145'; Prepare='21:44'; Submit='21:45'}
)
foreach ($entry in $schedule) {
    $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + `
        (Join-Path $PSScriptRoot 'Run-Scheduled.ps1') + '" -At ' + $entry.Submit
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arguments -WorkingDirectory $PSScriptRoot
    $trigger = New-ScheduledTaskTrigger -Daily -At $entry.Prepare
    $task = New-ScheduledTask -Action $action -Trigger $trigger -Settings $settings -Principal $principal `
        -Description ('HDU library: pre-login at ' + $entry.Prepare + ', submit at ' + $entry.Submit + ' Asia/Shanghai; skips disabled weekdays.')
    Register-ScheduledTask -TaskName $entry.Name -InputObject $task -Force | Out-Null
    # Keep local tasks disabled while validating GitHub Actions only.
    Disable-ScheduledTask -TaskName $entry.Name | Out-Null
}
foreach ($taskName in $legacyTaskNames) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}
Get-ScheduledTask -TaskName $taskNames | Select-Object TaskName,State
Write-Output 'Local tasks installed DISABLED for cloud-only testing. Configured times: 21:40 and 21:45 Beijing time.'
Write-Output 'Enable the desired booking weekdays in user_config.yml. Logs are stored in logs/.'
