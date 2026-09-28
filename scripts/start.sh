#!/usr/bin/env bash
# Запуск бота на Linux-сервере или в GitHub Codespaces:
#   bash scripts/start.sh
# Бот, PostgreSQL и Redis запускаются в Docker (Python 3.12 из Dockerfile).
# Если файла .env нет, он создаётся из переменных окружения (секретов Codespaces).
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  if [ -z "${TELEGRAM_BOT_TOKEN:-}" ]; then
    echo "Нет TELEGRAM_BOT_TOKEN: добавьте секрет в GitHub → Settings → Codespaces → Secrets" >&2
    echo "и пересоздайте codespace, либо создайте файл .env по образцу .env.example" >&2
    exit 1
  fi
  echo "Создаю .env из секретов Codespaces"
  : > .env
  for v in TELEGRAM_BOT_TOKEN OPENROUTER_API_KEY OPENROUTER_BASE_URL LLM_MODEL \
           FREELANCEHUNT_TOKEN FREELANCEHUNT_ONLY_MY_SKILLS DEMO_SOURCE_ENABLED; do
    if [ -n "${!v:-}" ]; then echo "$v=${!v}" >> .env; fi
  done
  chmod 600 .env
fi

docker compose --profile bot up -d --build
echo
echo "Бот запущен. Ниже его лог (Ctrl+C — выйти из лога, бот продолжит работать)."
echo "Остановить бота: docker compose --profile bot down"
echo
docker compose logs -f --tail 50 bot
