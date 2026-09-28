# Автозапуск бота при входе в Windows (задача в Планировщике заданий).
# Установить:  powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1
# Удалить:     powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Remove
param([switch]$Remove)
$name = "FreelanceRadarBot"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Автозапуск удалён."
    exit 0
}
$script = Join-Path $PSScriptRoot "run_forever.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Minimized -File `"$script`"" `
    -WorkingDirectory (Split-Path $PSScriptRoot -Parent)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "FreelanceRadar: запуск Telegram-бота при входе в систему" -Force | Out-Null
Write-Host "Готово: бот будет запускаться при входе в Windows (окно свернётся)."
Write-Host "Запустить сейчас: Start-ScheduledTask -TaskName $name"
