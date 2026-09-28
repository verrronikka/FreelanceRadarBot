"""Тестовый источник «Example (демо)».

Нужен, чтобы проверить весь поток без реальной площадки. Каждый цикл опроса
«публикует»:
  * 2 заказа из списка заготовок ниже (по кругу, разные профессии);
  * 1 заказ, собранный из ключевого слова одного из пользователей (по кругу),
    чтобы демо подходило под любую профессию — хоть «3д моделирование».
Ссылки ведут на example.com. Отключается DEMO_SOURCE_ENABLED=false.
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from bot.adapters.base import BaseAdapter, FetchResult, NormalizedJob, make_job

_TEMPLATES = [
    ("Монтаж ролика в After Effects", "Анимированный промо-ролик 60 сек в After Effects: титры, переходы, звук.", 25000, 35000),
    ("3D-модель персонажа в Blender", "Нужна 3D-модель персонажа для игры: скульпт, ретопология, текстуры. 3D моделирование под Unity.", 30000, 60000),
    ("Лендинг для онлайн-курса на Tilda", "Нужен лендинг на Tilda для курса по дизайну: 6 блоков, адаптив, форма заявки.", 25000, 35000),
    ("Тексты для сайта стоматологии", "Копирайтинг: 8 страниц, SEO-тексты, tone of voice дружелюбный.", 10000, 18000),
    ("Монтаж длинного видео для YouTube", "Интервью 1,5 часа: монтаж в Premiere Pro, цветокоррекция, субтитры.", 15000, 25000),
    ("3D-визуализация интерьера", "Визуализация кухни-гостиной 30 м², 3ds Max + Corona, 4 ракурса.", 20000, 35000),
    ("Python-бот для Telegram", "Сделать бота на aiogram 3 с PostgreSQL, приём заявок и админка.", 40000, 60000),
    ("Ведение Instagram кофейни", "SMM: контент-план, 12 постов и 20 сторис в месяц, фото на месте.", 20000, 30000),
    ("Моушн-дизайн для рекламы", "Motion design, After Effects, 3 версии под Reels и Shorts.", 30000, 50000),
    ("Дизайн мобильного приложения", "Figma, 12 экранов, фитнес-трекер, нужен UI-kit.", 50000, 80000),
    ("Перевод инструкции с английского", "Технический перевод 25 страниц EN→RU, оборудование для кофеен.", 12000, 20000),
    ("3D-модель для печати", "Смоделировать корпус устройства для 3D-печати по чертежу, STL.", 8000, 15000),
    ("Монтаж Reels для блогера", "10 вертикальных роликов в месяц, динамичный монтаж, субтитры.", 12000, 20000),
    ("Логотип для кофейни", "Разработать логотип и фирменные цвета для небольшой кофейни.", 5000, 10000),
    ("Backend на FastAPI", "REST API, PostgreSQL, Docker, авторизация JWT.", 70000, 100000),
    ("Ретушь каталожных фото", "Обработка 60 фото одежды на белом фоне, Photoshop.", 6000, 10000),
    ("Вёрстка landing page по макету", "Адаптивная вёрстка HTML/CSS по макету Figma, 1 страница.", 8000, 12000),
    ("Настройка рекламы в Яндекс Директ", "Запуск кампании для интернет-магазина, аудит текущей.", 20000, 30000),
]

# Шаблоны заказа «под ключевое слово»: {kw} — слово пользователя
_KEYWORD_TEMPLATES = [
    ("Нужен специалист: {kw}", "Ищем исполнителя по направлению «{kw}». Небольшой проект, нужно портфолио, старт на этой неделе."),
    ("Проект по теме «{kw}»", "Задача на 1–2 недели, направление «{kw}». Подробное ТЗ вышлем после отклика."),
    ("Разовая задача: {kw}", "Требуется помощь: {kw}. Оплата после сдачи, возможна долгосрочная работа."),
]

KeywordProvider = Callable[[], Awaitable[list[str]]]


class ExampleAdapter(BaseAdapter):
    source_code = "example"
    source_name = "Example (демо)"

    def __init__(self, keyword_provider: Optional[KeywordProvider] = None) -> None:
        self.keyword_provider = keyword_provider

    async def _keyword_job(self, counter: int, stamp: datetime) -> Optional[NormalizedJob]:
        if self.keyword_provider is None:
            return None
        keywords = await self.keyword_provider()
        if not keywords:
            return None
        kw = keywords[(counter // 2) % len(keywords)]
        title, desc = _KEYWORD_TEMPLATES[(counter // 2) % len(_KEYWORD_TEMPLATES)]
        bmin = random.choice([10000, 15000, 20000, 30000, 40000])
        ext_id = f"demo-{int(stamp.timestamp())}-kw"
        return make_job(
            external_id=ext_id,
            url=f"https://example.com/jobs/{ext_id}",
            title=title.format(kw=kw),
            description=desc.format(kw=kw),
            budget_min=bmin,
            budget_max=bmin * 2,
            currency="RUB",
            published_at=stamp,
        )

    async def fetch_new(self, cursor: Optional[str]) -> FetchResult:
        counter = int(cursor) if cursor and cursor.isdigit() else 0
        stamp = datetime.now(timezone.utc)
        items: list[NormalizedJob] = []
        for i in range(2):
            title, desc, bmin, bmax = _TEMPLATES[(counter + i) % len(_TEMPLATES)]
            ext_id = f"demo-{int(stamp.timestamp())}-{i}"
            items.append(
                make_job(
                    external_id=ext_id,
                    url=f"https://example.com/jobs/{ext_id}",
                    title=title,
                    description=desc,
                    budget_min=bmin,
                    budget_max=bmax,
                    currency="RUB",
                    published_at=stamp,
                )
            )
        kw_job = await self._keyword_job(counter, stamp)
        if kw_job is not None:
            items.append(kw_job)
        return FetchResult(source_code=self.source_code, cursor=cursor, items=items, next_cursor=str(counter + 2))
