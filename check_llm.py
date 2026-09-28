"""Проверка ключа ИИ и подбор адреса API: python check_llm.py

Берёт ключ из .env (OPENROUTER_API_KEY), перебирает типичные адреса
OpenAI-совместимого API (в т.ч. deepcode.ci.nsu.ru), показывает доступные модели,
делает тестовый запрос и пишет, какие строки вписать в .env.
"""
from __future__ import annotations

import asyncio
import time

import httpx

from core.config import settings

CANDIDATES = [
    settings.openrouter_base_url,
    "https://deepcode.ci.nsu.ru/v1",
    "https://deepcode.ci.nsu.ru/api/v1",
    "https://deepcode.ci.nsu.ru/api",
    "https://deepcode.ci.nsu.ru/openai/v1",
    "https://deepcode.ci.nsu.ru",
    "https://api.deepseek.com/v1",
    "https://openrouter.ai/api/v1",
]


def pick_model(models: list[str]) -> str | None:
    if not models:
        return None
    for pref in ("flash", "chat", "v4", "deepseek"):
        for m in models:
            if pref in m.lower():
                return m
    return models[0]


async def try_base(base: str, proxy: str | None) -> tuple[bool, str]:
    key = settings.openrouter_api_key
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=20, proxy=proxy, follow_redirects=True) as c:
        try:
            r = await c.get(f"{base}/models", headers=headers)
        except httpx.HTTPError as exc:
            return False, f"не отвечает ({type(exc).__name__})"
        models: list[str] = []
        if r.status_code == 200:
            try:
                models = [m.get("id") for m in r.json().get("data", []) if m.get("id")]
            except ValueError:
                return False, "отвечает, но это не API (не JSON)"
        elif r.status_code in (401, 403):
            return False, f"ключ не принят (HTTP {r.status_code})"
        elif r.status_code != 404:
            return False, f"HTTP {r.status_code}"

        model = pick_model(models) or settings.llm_model
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "Ответь одним словом: ок"}],
            "max_tokens": 200,
            "temperature": 0,
            "chat_template_kwargs": {"thinking": False, "enable_thinking": False},
        }
        t = time.monotonic()
        try:
            r2 = await c.post(f"{base}/chat/completions", json=body, headers=headers)
            if r2.status_code in (400, 422):
                body.pop("chat_template_kwargs")
                r2 = await c.post(f"{base}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as exc:
            return False, f"chat/completions не отвечает ({type(exc).__name__})"
        if r2.status_code != 200:
            return False, f"chat/completions → HTTP {r2.status_code}: {r2.text[:150]}"
        try:
            msg = r2.json()["choices"][0]["message"]
            answer = msg.get("content") or f"(пусто; рассуждение: {(msg.get('reasoning_content') or msg.get('reasoning') or '—')[:80]!r})"
        except (ValueError, KeyError, IndexError):
            return False, "ответ не в формате OpenAI"
        info = f"модели: {', '.join(models[:10]) or '—'}\n    тестовый ответ модели {model!r} за {time.monotonic() - t:.1f} с: {answer!r}"
        return True, f"{info}\n__MODEL__={model}"


async def try_bot_path(base: str, model: str, proxy: str | None) -> None:
    """Проверка тем же кодом, что использует бот: оценка тестового заказа."""
    from bot.services.llm_gateway import LLMGateway, LLMUnavailable

    settings.openrouter_base_url, settings.llm_model, settings.llm_proxy_url = base, model, proxy
    llm = LLMGateway()
    job = {"title": "Монтаж ролика в After Effects", "description": "Промо-ролик 60 сек, титры, звук",
           "budget_min": 25000, "budget_max": 35000, "currency": "RUB"}
    t = time.monotonic()
    try:
        v = await llm.evaluate("Монтирую рекламные ролики в After Effects", job)
        print(f"\nПроверка как в боте ({time.monotonic() - t:.1f} с): match={v.match}, score={v.score}, причины: {v.reasons}")
        if time.monotonic() - t > settings.llm_timeout_seconds:
            print(f"Внимание: дольше LLM_TIMEOUT_SECONDS={settings.llm_timeout_seconds} — в боте сработает запасной вариант.")
    except LLMUnavailable as exc:
        print(f"\nПроверка как в боте: ИИ недоступен ({exc}) — бот будет работать на фильтрах.")
    finally:
        await llm.close()


async def main() -> None:
    if not settings.openrouter_api_key:
        print("В .env нет OPENROUTER_API_KEY — впишите ключ и запустите снова.")
        return
    proxies: list[str | None] = [None]
    if settings.telegram_proxy_url and settings.telegram_proxy_url.startswith("http"):
        proxies.append(settings.telegram_proxy_url)

    seen = set()
    for base in CANDIDATES:
        base = base.rstrip("/")
        if base in seen:
            continue
        seen.add(base)
        for proxy in proxies:
            ok, info = await try_base(base, proxy)
            via = "через прокси" if proxy else "напрямую"
            print(f"{'✅' if ok else '❌'} {base} ({via}): {info.split('__MODEL__')[0].strip()}")
            if ok:
                model = info.split("__MODEL__=")[1]
                print("\nРаботает! Впишите в .env (замените старые строки):\n")
                print(f"OPENROUTER_BASE_URL={base}")
                print(f"LLM_MODEL={model}")
                print(f"LLM_PROXY_URL={'none' if proxy is None else proxy}")
                await try_bot_path(base, model, proxy)
                return
    print("\nНи один адрес не подошёл. Если сервис НГУ доступен только из сети университета — "
          "запускайте бота из сети НГУ или спросите у преподавателя адрес API (обычно .../v1).")


if __name__ == "__main__":
    asyncio.run(main())
