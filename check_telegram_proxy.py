"""Диагностика подключения к Telegram тем же кодом, что и в боте.

Запуск:  python check_telegram_proxy.py
Проверяет: прямое подключение, прокси из .env и типовые порты Happ/v2rayN
(http 10809, socks5 10808). Подсказывает, что вписать в TELEGRAM_PROXY_URL.
"""
from __future__ import annotations

import asyncio

from aiogram import Bot

from bot.telegram_session import ProxyAwareSession
from core.config import mask_url, settings

CANDIDATES = [
    None,
    "http://127.0.0.1:10809",
    "socks5://127.0.0.1:10808",
    "http://127.0.0.1:7890",
    "socks5://127.0.0.1:7890",
    "socks5://127.0.0.1:1080",
]


async def try_proxy(proxy: str | None) -> str:
    bot = Bot(token=settings.telegram_bot_token, session=ProxyAwareSession(proxy_url=proxy))
    try:
        me = await bot.get_me(request_timeout=8)
        return f"OK  (бот @{me.username})"
    except Exception as exc:
        return f"нет — {type(exc).__name__}: {str(exc)[:120]}"
    finally:
        await bot.session.close()


async def main() -> None:
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN не задан в .env")
        return
    proxies = list(CANDIDATES)
    if settings.telegram_proxy_url and settings.telegram_proxy_url not in proxies:
        proxies.insert(1, settings.telegram_proxy_url)
    print(f"В .env сейчас: TELEGRAM_PROXY_URL={mask_url(settings.telegram_proxy_url)}\n")
    working = []
    for proxy in proxies:
        result = await try_proxy(proxy)
        print(f"{(mask_url(proxy) if proxy else 'без прокси'):32} {result}")
        if result.startswith("OK"):
            working.append(proxy)
    print()
    if not working:
        print("Ничего не сработало: проверьте, что VPN-клиент запущен и токен верный.")
    elif None in working:
        print("Telegram доступен напрямую — строку TELEGRAM_PROXY_URL можно закомментировать.")
    else:
        print(f"Впишите в .env:  TELEGRAM_PROXY_URL={working[0]}")


if __name__ == "__main__":
    asyncio.run(main())
