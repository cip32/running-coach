# Registers (or removes) a Windows Task Scheduler job that runs
#   coach sync && coach report
# every Sunday at 19:00 local time. Make sure Windows is set to Berlin time
# (`coach doctor` checks this). Runs late if the PC was off at 19:00.
#
#   powershell -ExecutionPolicy Bypass -File scheduler\install_windows_task.ps1
#   powershell -ExecutionPolicy Bypass -File scheduler\install_windows_task.ps1 -Remove

param(
    [switch]$Remove,
    [string]$TaskName = "RunningCoachWeeklyReport",
    [string]$Time = "19:00"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "Removed scheduled task '$TaskName'."
    exit 0
}

$coach = Join-Path $root ".venv\Scripts\coach.exe"
if (-not (Test-Path $coach)) { throw "coach.exe not found at $coach. Run the README setup first." }
$log = Join-Path $root "data\scheduled.log"
$cmd = "/c cd /d `"$root`" && `"$coach`" sync >> `"$log`" 2>&1 && `"$coach`" report >> `"$log`" 2>&1"

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmd -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Running coach: Garmin sync + weekly report (Sunday $Time)" -Force | Out-Null
Write-Output "Scheduled '$TaskName' for Sundays at $Time. Log: $log"
