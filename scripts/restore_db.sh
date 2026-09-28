#!/usr/bin/env bash
# Восстановление базы из копии. Остановите бота перед запуском!
# Запуск: bash scripts/restore_db.sh backups/freelanceradar_....sql.gz
set -euo pipefail
file="${1:?Укажите файл копии: bash scripts/restore_db.sh backups/<файл>.sql.gz}"
gunzip -c "$file" | docker exec -i fr_postgres psql -q -U fr_user -d freelanceradar
echo "База восстановлена из $file"
