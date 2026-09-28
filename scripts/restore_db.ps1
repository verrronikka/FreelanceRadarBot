# Восстановление базы из копии (Windows). Остановите бота перед запуском!
# Запуск:  powershell -ExecutionPolicy Bypass -File scripts\restore_db.ps1 backups\freelanceradar_....sql
param([Parameter(Mandatory=$true)][string]$File)
$ErrorActionPreference = "Stop"
docker cp $File fr_postgres:/tmp/restore.sql
docker exec fr_postgres psql -q -U fr_user -d freelanceradar -f /tmp/restore.sql
docker exec fr_postgres rm /tmp/restore.sql
Write-Host "База восстановлена из $File"
