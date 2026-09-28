# FreelanceRadar — Telegram-бот мониторинга заказов

Бот следит за новыми заказами в разрешённых источниках, сверяет их с профилем
пользователя (бюджет, ключевые слова, исключения, опционально ИИ-оценка через
OpenRouter) и присылает карточку с кнопкой «Откликнуться». Техзадание — `doc.txt`.

## Что нужно

- Python 3.12
- Docker Desktop (для PostgreSQL и Redis)
- Токен бота от [@BotFather](https://t.me/BotFather)
- Если Telegram не открывается напрямую — запущенный VPN-клиент с локальным прокси
  (Happ / v2rayN: http `127.0.0.1:10809`, socks5 `127.0.0.1:10808`)

## Запуск на Windows (PowerShell)

Все команды — из папки проекта `C:\FreelanceRadarBot`.

**1. Виртуальное окружение и зависимости** (один раз)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Если PowerShell ругается на запуск скриптов:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

**2. Настройки** (один раз)

```powershell
copy .env.example .env
notepad .env
```

Минимум — вписать `TELEGRAM_BOT_TOKEN`. Если нужен прокси — раскомментировать
`TELEGRAM_PROXY_URL`. Какой адрес подходит, покажет:

```powershell
python check_telegram_proxy.py
```

**3. База и Redis**

Запустить Docker Desktop, затем:

```powershell
docker compose up -d
```

Проверка: `docker compose ps` — оба контейнера `healthy`.

**4. Бот**

```powershell
.\.venv\Scripts\Activate.ps1
python -m bot.main
```

В логе должно быть:

```
Telegram: подключён как @имя_бота
PostgreSQL: таблицы готовы
Redis подключён: ...
Бот запущен. Откройте @имя_бота в Telegram и нажмите /start
```

Таблицы создаются автоматически при старте. Остановка — `Ctrl+C`.

**5. Проверка сценария**

1. В Telegram: `/start` → «Настроить профиль».
2. Название → источник «Example (демо)» → бюджет (например `10000`) →
   ключевые слова (например `лендинг, python, -логотип`) → описание для ИИ или «Пропустить» → «Подтвердить».
3. Демо-источник раз в `POLL_INTERVAL_SECONDS` (по умолчанию 60 с) публикует
   тестовый заказ — подходящие придут карточкой. Ссылки ведут на example.com.
4. «Пауза уведомлений», «Последние подборки», «Не показывать похожее», `/delete` — тоже работают.

Демо-источник выключается `DEMO_SOURCE_ENABLED=false`.

## Частые проблемы

| Сообщение в логе | Что делать |
|---|---|
| `Не удалось подключиться к Telegram API: Request timeout error` | Telegram недоступен: запустите VPN, выполните `python check_telegram_proxy.py` и впишите предложенный `TELEGRAM_PROXY_URL` |
| `Unauthorized` | Неверный `TELEGRAM_BOT_TOKEN` |
| `Не удалось подключиться к PostgreSQL` | Не запущен Docker / `docker compose up -d` |
| `Redis недоступен` | Бот работает, но состояния диалогов в памяти. Запустите `docker compose up -d` |
| `TelegramConflictError` | Бот уже запущен в другом окне — закройте лишний процесс |

## ИИ-фильтр (необязательно)

Впишите `OPENROUTER_API_KEY` и при желании `LLM_MODEL`. ИИ включается, только если
пользователь заполнил «Описание для ИИ» (с согласием на передачу текста провайдеру).
Если OpenRouter не ответил за `LLM_TIMEOUT_SECONDS` или вернул ошибку, решение
принимается по фильтрам, а в карточке будет «подобрано по фильтрам».

## Свои источники

Источник добавляется прямо в боте: «📡 Мои источники» → «➕ Добавить источник»
(или кнопка на шаге «Источники» при настройке профиля). Можно прислать ссылку на
RSS/Atom-ленту или на обычную страницу со списком заказов — бот сам найдёт на ней
RSS-ленту или вытащит заказы из ссылок.

Перед добавлением бот проверяет:
- что это внешний сайт (localhost и локальная сеть запрещены);
- robots.txt площадки — если автоматическое чтение запрещено, источник не добавится;
- что сайт не закрыт для ботов (401/403) — обходить защиту бот не будет;
- что по ссылке есть RSS или список заказов.

Добавленный источник видят все пользователи. Опрос — не чаще раза в минуту.
При первом опросе бот запоминает все заказы, а присылает только 5 самых свежих подходящих.

Freelancehunt подключается через официальный API: токен с https://freelancehunt.com/my/api2
в `FREELANCEHUNT_TOKEN`. Постоянную RSS-ленту можно задать в `.env` (`SOURCE_A_BASE_URL`).

Реестр источников с правилами и лимитами — `SOURCES.md`. Список из базы, включая
источники пользователей: `python -m bot.sources_report --write` → `sources_registry.md`.

## Запуск в GitHub Codespaces (без своего компьютера)

Подходит для защиты и демонстрации: сервер GitHub за границей, прокси не нужен.
Бот, PostgreSQL и Redis запускаются в Docker, который в Codespaces уже есть.

1. Отправьте код на GitHub: `git add . && git commit -m "..." && git push`.
2. Добавьте секреты: GitHub → Settings (вашего профиля) → Codespaces → Secrets → New secret:
   `TELEGRAM_BOT_TOKEN` и при желании `OPENROUTER_API_KEY`, `OPENROUTER_BASE_URL`, `LLM_MODEL`,
   `FREELANCEHUNT_TOKEN`. В «Repository access» выберите репозиторий бота.
3. **Остановите бота на своём компьютере** — один токен нельзя запускать в двух местах
   (будет `TelegramConflictError`).
4. На странице репозитория: Code → Codespaces → Create codespace on main (первый запуск 1–2 минуты).
5. В терминале codespace:
   ```bash
   bash scripts/start.sh
   ```
   Скрипт создаст `.env` из секретов, соберёт и запустит бота и покажет его лог.
   Остановить: `docker compose --profile bot down`.

Codespace засыпает без активности (по умолчанию через 30 минут). Пока открыт лог, бот каждую
минуту что-то пишет, и это считается активностью; надёжнее поднять таймаут до 240 минут:
GitHub → Settings → Codespaces → Default idle timeout. Бесплатно — около 60 часов в месяц
на машине с 2 ядрами. После защиты остановите codespace (Code → Codespaces → … → Stop).

## Запуск на сервере (Docker)

```bash
docker compose --profile bot up -d --build
```

Внутри контейнера `127.0.0.1` — это сам контейнер: на VPS прокси обычно не нужен,
уберите `TELEGRAM_PROXY_URL` из `.env`. Контейнер бота сам проверяет своё здоровье
(`docker compose ps` покажет `healthy`).

## Проверка работоспособности

- В Telegram: `/status` — сколько работает бот, когда был последний опрос каждого источника,
  сколько новых заказов и совпадений, подключён ли ИИ.
- В консоли (при запущенном боте): `python -m bot.healthcheck` — проверяет, что планировщик жив
  (пульс не старше 3 минут), PostgreSQL и Redis отвечают. Код выхода 0 — всё хорошо, 1 — проблема.

## Резервная копия базы

```powershell
# Windows
powershell -ExecutionPolicy Bypass -File scripts\backup_db.ps1
powershell -ExecutionPolicy Bypass -File scripts\restore_db.ps1 backups\<файл>.sql
```
```bash
# Linux / Codespaces
bash scripts/backup_db.sh
bash scripts/restore_db.sh backups/<файл>.sql.gz
```

Копии кладутся в `backups/` (в git не попадают), хранятся последние 7.
Перед восстановлением остановите бота.

## Тесты

```powershell
pip install -r requirements-dev.txt
pytest
```

Тесты не ходят в интернет, Telegram и базу. Проверяют: контракт адаптеров (RSS, страницы,
Freelancehunt, демо), проверку ссылок (robots.txt, 403, локальные адреса), правила
matched/rejected и деградацию при недоступном ИИ, повторы при ошибках источника и Telegram 429/5xx,
валидацию ввода профиля.

## Миграции Alembic (необязательно)

Бот сам создаёт таблицы. Если хотите вести схему через Alembic на уже созданной ботом базе:

```powershell
alembic stamp head      # один раз: пометить текущую схему как актуальную
alembic upgrade head    # далее — применять новые миграции
```

## Структура

```
bot/main.py               запуск, проверки, DI
bot/telegram_session.py   HTTP/SOCKS-прокси для Telegram
bot/handlers/start.py     команды, мастер профиля, меню
bot/scheduler.py          опрос источников, lease, retry, matching, доставка
bot/services/matching.py  правила + ИИ-оценка, порог 0.70
bot/services/llm_gateway.py  OpenRouter + Pydantic-валидация ответа
bot/services/delivery.py  отправка карточек, повторы при 429/5xx
bot/adapters/             контракт адаптера, демо, RSS, страницы сайтов, Freelancehunt API
bot/healthcheck.py        проверка работоспособности
bot/sources_report.py     выгрузка реестра источников
scripts/                  запуск на сервере, резервные копии
tests/                    автотесты
db/                       модели, репозиторий
```

Отличие от документации: вместо Celery фоновые задачи выполняются асинхронно
внутри процесса бота (Celery официально не поддерживает Windows). Гарантии те же:
lease в Redis, повторы с экспоненциальной паузой, отсутствие дублей.
