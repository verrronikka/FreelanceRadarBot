# Держит бота запущенным без присмотра (несколько дней на своём компьютере):
#  - ждёт, пока запустится Docker Desktop;
#  - поднимает PostgreSQL и Redis;
#  - запускает бота и перезапускает его через 30 с, если он упал или не смог подключиться
#    (например, Happ ещё не запустился после перезагрузки);
#  - пишет лог в logs\bot.log.
# Запуск:  powershell -ExecutionPolicy Bypass -File scripts\run_forever.ps1
# Остановка: закрыть окно или Ctrl+C.
$ErrorActionPreference = "Continue"
Set-Location (Join-Path $PSScriptRoot "..")
$env:LOG_FILE = "logs\bot.log"
$env:PYTHONUTF8 = "1"
$python = ".\.venv\Scripts\python.exe"

while ($true) {
    while (-not (docker info --format "{{.ServerVersion}}" 2>$null)) {
        Write-Host "$(Get-Date -Format 'HH:mm:ss') Жду Docker Desktop..."
        Start-Sleep -Seconds 15
    }
    docker compose up -d postgres redis | Out-Null
    Write-Host "$(Get-Date -Format 'HH:mm:ss') Запускаю бота"
    & $python -m bot.main
    Write-Host "$(Get-Date -Format 'HH:mm:ss') Бот остановился (код $LASTEXITCODE). Перезапуск через 30 с — Ctrl+C, чтобы выйти."
    Start-Sleep -Seconds 30
}
