#!/usr/bin/env bash
# Резервная копия PostgreSQL из контейнера fr_postgres в папку backups/.
# Хранит последние 7 копий. Запуск: bash scripts/backup_db.sh
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p backups
file="backups/freelanceradar_$(date +%Y-%m-%d_%H-%M-%S).sql.gz"
docker exec fr_postgres pg_dump -U fr_user -d freelanceradar --clean --if-exists | gzip > "$file"
echo "Готово: $file ($(du -h "$file" | cut -f1))"
ls -1t backups/freelanceradar_*.sql.gz | tail -n +8 | xargs -r rm --
