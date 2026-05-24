$taskName = "AlgoRich_Bot"
$batPath  = "C:\Algorich\start_bot.bat"

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$batPath`"" -WorkingDirectory "C:\Algorich"
$trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "08:00AM"
$settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 10) -StartWhenAvailable
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "AlgoRich NH-Sniper bot"

Write-Host "Done! AlgoRich_Bot registered - runs Mon-Fri 08:00" -ForegroundColor Green
Write-Host "Test now: Start-ScheduledTask -TaskName 'AlgoRich_Bot'"
