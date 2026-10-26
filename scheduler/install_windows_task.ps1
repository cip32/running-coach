# Registers (or removes) the Windows Task Scheduler job for the weekly report.
#
# It runs `coach weekly` (sync + report for the due week, skipped if already
# done) on three triggers:
#   - every Sunday at 19:00 local time
#   - when you log on
#   - when you unlock the PC
# so a Sunday missed because the PC was asleep or off is caught up the next
# time you use it. Repeat runs exit immediately once the report exists.
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

# python.exe + cli.py rather than coach.exe: some security setups block the
# generated launcher executable but allow the venv interpreter
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Python not found at $python. Run the README setup first." }
$log = Join-Path $root "data\scheduled.log"
$cmd = "/c cd /d `"$root`" && `"$python`" cli.py weekly >> `"$log`" 2>&1"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmd -WorkingDirectory $root

$weekly = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At $Time
# Store the start time without a UTC offset so it follows local clock time across
# daylight-saving changes (otherwise 19:00 summer time fires at 18:00 in winter).
$weekly.StartBoundary = (Get-Date $Time).ToString("yyyy-MM-dd'T'HH:mm:ss")

$logon = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$stateClass = Get-CimClass -Namespace Root/Microsoft/Windows/TaskScheduler -ClassName MSFT_TaskSessionStateChangeTrigger
$unlock = New-CimInstance -CimClass $stateClass -ClientOnly -Property @{
    StateChange = 8                                  # TASK_SESSION_UNLOCK
    UserId      = "$env:USERDOMAIN\$env:USERNAME"
    Enabled     = $true
}

$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($weekly, $logon, $unlock) `
    -Settings $settings -Description "Running coach: weekly Garmin sync + report (Sunday $Time, caught up at logon/unlock)" `
    -Force | Out-Null
Write-Output "Scheduled '$TaskName': Sundays at $Time, plus catch-up at logon and unlock. Log: $log"
