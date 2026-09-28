# Резервная копия PostgreSQL (Windows PowerShell). Хранит последние 7 копий.
# Запуск из папки проекта:  powershell -ExecutionPolicy Bypass -File scripts\backup_db.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
New-Item -ItemType Directory -Force -Path backups | Out-Null
$file = "backups\freelanceradar_{0}.sql" -f (Get-Date -Format "yyyy-MM-dd_HH-mm-ss")
# Копируем дамп через файл внутри контейнера, чтобы PowerShell не испортил кодировку
docker exec fr_postgres pg_dump -U fr_user -d freelanceradar --clean --if-exists -f /tmp/backup.sql
docker cp fr_postgres:/tmp/backup.sql $file
docker exec fr_postgres rm /tmp/backup.sql
Write-Host "Готово: $file"
Get-ChildItem backups\freelanceradar_*.sql | Sort-Object LastWriteTime -Descending | Select-Object -Skip 7 | Remove-Item
