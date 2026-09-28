"""Команды и пошаговая настройка профиля (сценарий A, раздел 6 документации)."""
from __future__ import annotations

import logging
import time
import uuid
from html import escape
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards.main import (
    cancel_keyboard,
    confirmation_keyboard,
    delete_confirm_keyboard,
    empty_state_keyboard,
    job_card_keyboard,
    add_source_cancel_keyboard,
    my_sources_keyboard,
    main_menu_keyboard,
    skip_cancel_keyboard,
    sources_keyboard,
)
from bot.services.delivery import fmt_money, render_card
from bot.adapters.discovery import check_source
from bot.adapters.rss import FeedCheckError
from bot.states import AddSource, HideSimilar, ProfileSetup
from core.config import settings
from db.models import User
from db.repository import (
    add_exclusion,
    create_feed_source,
    subscribe_profile,
    unsubscribe_profile,
    delete_user_data,
    get_decision_stats,
    get_active_sources,
    get_job,
    get_or_create_user,
    get_recent_matches,
    get_user,
    get_user_profile,
    save_profile,
)

logger = logging.getLogger(__name__)
router = Router(name="main")

EMPTY_TEXT = "Подключите источник и настройте профиль — я начну следить за новыми заказами."
SKIP_WORDS = {"пропустить", "skip", "-", "нет"}
MAX_KEYWORDS = 20
MAX_AI_PROMPT = 1500
MAX_BUDGET = 10_000_000


# ------------------------------------------------------------------ helpers

TOTAL_STEPS = 5


def step(n: int, title: str) -> str:
    """Заголовок шага мастера с прогресс-баром."""
    return f"<b>Шаг {n}/{TOTAL_STEPS} · {title}</b>\n{'▰' * n}{'▱' * (TOTAL_STEPS - n)}\n\n"


def ai_status(enabled: bool) -> str:
    if not enabled:
        return "выключен"
    return "включён" if settings.llm_configured else "включён, но нет ключа OpenRouter — работают фильтры"


async def _menu_text(session: AsyncSession, user: User) -> tuple[str, bool]:
    profile = await get_user_profile(session, user.id)
    if profile is None:
        return f"📡 <b>FreelanceRadar</b>\n\n{EMPTY_TEXT}", False
    kws = [r.value for r in profile.filter_rules if not r.is_exclusion]
    notif = "⏸ на паузе (сбор заказов продолжается)" if user.notifications_paused else "🔔 включены"
    text = (
        "📡 <b>FreelanceRadar</b>\n\n"
        f"👤 Профиль: <b>{escape(profile.name)}</b>\n"
        f"🔎 Ключевые слова: {escape(', '.join(kws)) if kws else 'любые'}\n"
        f"💰 Бюджет от: {fmt_money(profile.min_budget) + ' ₽' if profile.min_budget else 'не важен'}\n"
        f"🌐 Источников: {len(profile.sources)}\n"
        f"🤖 ИИ-фильтр: {ai_status(profile.ai_enabled)}\n"
        f"Уведомления: {notif}"
    )
    return text, True


async def send_menu(message: Message, session: AsyncSession, user: User) -> None:
    text, has_profile = await _menu_text(session, user)
    kb = main_menu_keyboard(user.notifications_paused) if has_profile else empty_state_keyboard()
    await message.answer(text, parse_mode="HTML", reply_markup=kb)


def parse_keywords(text: str) -> tuple[list[str], list[str], str | None]:
    """'лендинг, tilda, -логотип' -> (keywords, exclusions, error)."""
    keywords: list[str] = []
    exclusions: list[str] = []
    for raw in text.replace(";", ",").replace("\n", ",").split(","):
        word = " ".join(raw.split()).lower()
        if not word:
            continue
        target = keywords
        if word.startswith("-"):
            word = word[1:].strip()
            target = exclusions
        if not 2 <= len(word) <= 64:
            return [], [], f"Слово «{escape(word)}» должно быть от 2 до 64 символов."
        if word not in target:
            target.append(word)
    if len(keywords) + len(exclusions) > MAX_KEYWORDS:
        return [], [], f"Слишком много слов — максимум {MAX_KEYWORDS}."
    return keywords, exclusions, None


def summary_text(data: dict[str, Any], source_names: list[str]) -> str:
    budget = data.get("min_budget", 0)
    kws = data.get("keywords") or []
    exc = data.get("exclusions") or []
    lines = [
        "📋 <b>Проверьте профиль</b>",
        "",
        f"👤 <b>{escape(data['profile_name'])}</b>",
        f"🌐 Источники: {escape(', '.join(source_names)) if source_names else 'не выбраны'}",
        f"💰 Бюджет от: {fmt_money(budget) + ' ₽' if budget else 'не важен'}",
        f"🔎 Ключевые слова: {escape(', '.join(kws)) if kws else 'любые'}",
    ]
    if exc:
        lines.append(f"🚫 Исключения: {escape(', '.join(exc))}")
    lines.append(f"🤖 ИИ-фильтр: {ai_status(bool(data.get('ai_enabled')))}")
    if data.get("ai_prompt"):
        lines += ["", f"<blockquote>{escape(data['ai_prompt'])}</blockquote>"]
    return "\n".join(lines)


# ------------------------------------------------------------------ commands


@router.message(CommandStart())
async def cmd_start(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await get_or_create_user(session, message.from_user.id)
    await session.commit()
    await message.answer(
        "👋 <b>Привет! Я FreelanceRadar.</b>\n"
        "Слежу за новыми заказами и присылаю только подходящие — по бюджету, ключевым словам и описанию."
    )
    await send_menu(message, session, user)


@router.message(Command("menu"))
async def cmd_menu(message: Message, session: AsyncSession, state: FSMContext) -> None:
    await state.clear()
    user = await get_or_create_user(session, message.from_user.id)
    await session.commit()
    await send_menu(message, session, user)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(
        "/start — главное меню\n"
        "/menu — показать меню\n"
        "/test — прислать тестовое уведомление\n"
        "/status — состояние бота и источников\n"
        "/delete — удалить профиль и настройки\n"
        "/ping — проверить, что бот жив"
    )


@router.message(Command("test"))
async def cmd_test(message: Message, scheduler: Any = None) -> None:
    """Проверка доставки: карточка приходит сразу, без фильтров."""
    await message.answer(
        "🧪 <b>Тестовое уведомление</b> · Example (демо)\n\n"
        "<b>Монтаж ролика в After Effects</b>\n\n"
        "💰 25 000 – 35 000 ₽\n"
        "🕒 только что\n"
        "<blockquote>✨ Так будут выглядеть карточки подходящих заказов</blockquote>",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🚀 Откликнуться", url="https://example.com/jobs/test")]]
        ),
    )
    if scheduler is not None:
        scheduler.poll_soon()


def _ago(ts: float | None) -> str:
    if not ts:
        return "ещё не было"
    sec = int(time.time() - ts)
    if sec < 60:
        return f"{sec} с назад"
    if sec < 3600:
        return f"{sec // 60} мин назад"
    return f"{sec // 3600} ч назад"


@router.message(Command("status"))
async def cmd_status(message: Message, scheduler: Any = None) -> None:
    """Состояние бота: источники, последние опросы, ИИ — удобно показать на защите."""
    if scheduler is None:
        await message.answer("Планировщик не запущен.")
        return
    snap = scheduler.snapshot()
    up = int(time.time() - snap["started_at"])
    lines = [
        "🩺 <b>Состояние FreelanceRadar</b>",
        "",
        f"⏱ Работает: {up // 3600} ч {up % 3600 // 60} мин",
        f"🤖 ИИ-фильтр: {'подключён' if snap['llm_enabled'] else 'не настроен — работают фильтры'}",
        f"📨 Отправлено уведомлений с запуска: {snap['sent_total']}",
        "",
        "<b>Источники</b>",
    ]
    if not snap["sources"]:
        lines.append("пока не опрашивались")
    for s in snap["sources"].values():
        icon = "🟢" if s["last_ok_at"] and not s["last_error"] else ("🔴" if s["last_error"] else "⚪️")
        err = f" · ошибка {escape(s['last_error'])}" if s["last_error"] else ""
        lines.append(
            f"{icon} {escape(s['name'])}: опрос {_ago(s['last_ok_at'])}, "
            f"новых {s['jobs_new']}, совпадений {s['matched']}{err}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    await message.answer("pong")


# ------------------------------------------------------------------ wizard


@router.callback_query(F.data == "setup_profile")
async def start_profile_setup(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user = await get_or_create_user(session, callback.from_user.id)
    await session.commit()
    profile = await get_user_profile(session, user.id)
    await state.set_state(ProfileSetup.waiting_for_name)
    hint = f"\n\nСейчас: <b>{escape(profile.name)}</b> (настройки будут перезаписаны)" if profile else ""
    await callback.message.answer(
        step(1, "Название") + "Как назвать профиль?\n<i>Например: Монтаж видео, Дизайн лендингов</i>" + hint,
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )
    await callback.answer()


@router.message(ProfileSetup.waiting_for_name, F.text)
async def process_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    name = " ".join(message.text.split())
    if not 2 <= len(name) <= 120:
        await message.answer("Название должно быть от 2 до 120 символов. Попробуйте ещё раз:")
        return
    await state.update_data(profile_name=name, selected_sources=[])
    sources = await get_active_sources(session)
    if not sources:
        await state.set_state(ProfileSetup.waiting_for_budget)
        await message.answer(
            "⚠️ Сейчас нет активных источников — профиль сохранится, подключите источник позже.\n\n"
            + step(3, "Бюджет") + "Минимальная сумма заказа в рублях.\n<i>Например: 20000</i>",
            reply_markup=skip_cancel_keyboard(),
        )
        return
    await state.set_state(ProfileSetup.waiting_for_sources)
    await message.answer(
        step(2, "Источники") + "Где следить за заказами? Отметьте и нажмите «Готово».",
        reply_markup=sources_keyboard(sources, []),
    )


@router.callback_query(ProfileSetup.waiting_for_sources, F.data.startswith("toggle_source:"))
async def toggle_source(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    source_id = int(callback.data.split(":", 1)[1])
    sources = await get_active_sources(session)
    if source_id not in {s.id for s in sources}:  # разрешаются только active sources
        await callback.answer("Источник недоступен", show_alert=True)
        return
    selected: list[int] = list((await state.get_data()).get("selected_sources", []))
    if source_id in selected:
        selected.remove(source_id)
    else:
        selected.append(source_id)
    await state.update_data(selected_sources=selected)
    await callback.message.edit_reply_markup(reply_markup=sources_keyboard(sources, selected))
    await callback.answer()


@router.callback_query(ProfileSetup.waiting_for_sources, F.data == "sources_done")
async def sources_done(callback: CallbackQuery, state: FSMContext) -> None:
    selected = (await state.get_data()).get("selected_sources", [])
    if not selected:
        await callback.answer("Выберите хотя бы один источник", show_alert=True)
        return
    await state.set_state(ProfileSetup.waiting_for_budget)
    await callback.message.edit_text(
        f"✅ Выбрано источников: {len(selected)}\n\n"
        + step(3, "Бюджет") + "Минимальная сумма заказа в рублях.\n<i>Например: 20000</i>",
        reply_markup=skip_cancel_keyboard(),
    )
    await callback.answer()


async def _ask_keywords(message: Message, state: FSMContext, budget: int) -> None:
    await state.update_data(min_budget=budget)
    await state.set_state(ProfileSetup.waiting_for_keywords)
    head = f"✅ Бюджет от {fmt_money(budget)} ₽" if budget else "✅ Бюджет не важен"
    await message.answer(
        f"{head}\n\n" + step(4, "Ключевые слова") +
        "Через запятую — заказ подойдёт, если в нём есть хотя бы одно.\n"
        "<i>Например: монтаж, after effects, reels</i>\n\n"
        "🚫 Исключения — с минусом: <i>-логотип, -seo</i>",
        reply_markup=skip_cancel_keyboard(),
    )


@router.message(ProfileSetup.waiting_for_budget, F.text)
async def process_budget(message: Message, state: FSMContext) -> None:
    text = message.text.strip().lower().replace(" ", "").replace("₽", "").replace("руб", "")
    if text in SKIP_WORDS:
        await _ask_keywords(message, state, 0)
        return
    if not text.isdigit() or int(text) > MAX_BUDGET:
        await message.answer("Бюджет должен быть целым числом от 0 до 10 000 000.")
        return
    await _ask_keywords(message, state, int(text))


async def _ask_ai(message: Message, state: FSMContext) -> None:
    await state.set_state(ProfileSetup.waiting_for_ai_prompt)
    llm_note = "" if settings.llm_configured else "\n\n⚠️ Ключ OpenRouter не настроен — пока будут работать только фильтры."
    await message.answer(
        step(5, "Описание для ИИ") +
        "Опишите своими словами, какие заказы ищете — это необязательно.\n"
        "<i>Например: монтирую длинные видео для YouTube, не беру рекламу</i>\n\n"
        "<blockquote>Отправляя описание, вы соглашаетесь, что его текст и тексты заказов "
        "будут передаваться LLM-провайдеру (OpenRouter) для оценки.</blockquote>" + llm_note,
        reply_markup=skip_cancel_keyboard(),
    )


@router.message(ProfileSetup.waiting_for_keywords, F.text)
async def process_keywords(message: Message, state: FSMContext) -> None:
    if message.text.strip().lower() in SKIP_WORDS:
        await state.update_data(keywords=[], exclusions=[])
        await _ask_ai(message, state)
        return
    keywords, exclusions, error = parse_keywords(message.text)
    if error:
        await message.answer(f"{error} Попробуйте ещё раз:", parse_mode="HTML")
        return
    await state.update_data(keywords=keywords, exclusions=exclusions)
    await _ask_ai(message, state)


async def _show_summary(message: Message, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    ids = set(data.get("selected_sources", []))
    names = [s.name for s in await get_active_sources(session) if s.id in ids]
    await state.set_state(ProfileSetup.confirmation)
    await message.answer(
        summary_text(data, names) + "\n\nВсё верно?", parse_mode="HTML", reply_markup=confirmation_keyboard()
    )


@router.message(ProfileSetup.waiting_for_ai_prompt, F.text)
async def process_ai_prompt(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = message.text.strip()
    if text.lower() in SKIP_WORDS:
        await state.update_data(ai_prompt=None, ai_enabled=False)
    elif len(text) > MAX_AI_PROMPT:
        await message.answer(f"Описание слишком длинное ({len(text)} символов, максимум {MAX_AI_PROMPT}). Сократите:")
        return
    else:
        await state.update_data(ai_prompt=text, ai_enabled=True)
    await _show_summary(message, state, session)


@router.callback_query(F.data == "skip_step")
async def skip_step(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    current = await state.get_state()
    await callback.answer()
    await callback.message.edit_reply_markup(reply_markup=None)
    if current == ProfileSetup.waiting_for_budget.state:
        await _ask_keywords(callback.message, state, 0)
    elif current == ProfileSetup.waiting_for_keywords.state:
        await state.update_data(keywords=[], exclusions=[])
        await _ask_ai(callback.message, state)
    elif current == ProfileSetup.waiting_for_ai_prompt.state:
        await state.update_data(ai_prompt=None, ai_enabled=False)
        await _show_summary(callback.message, state, session)


@router.callback_query(ProfileSetup.confirmation, F.data == "confirm_profile")
async def confirm_profile(
    callback: CallbackQuery, state: FSMContext, session: AsyncSession, scheduler: Any = None
) -> None:
    data = await state.get_data()
    await state.clear()  # повторное нажатие уже не попадёт сюда
    await callback.answer("Сохраняю настройки…")
    await callback.message.edit_reply_markup(reply_markup=None)

    user = await get_or_create_user(session, callback.from_user.id)
    active_ids = {s.id for s in await get_active_sources(session)}
    await save_profile(
        session,
        user=user,
        name=data["profile_name"],
        source_ids=[i for i in data.get("selected_sources", []) if i in active_ids],
        min_budget=data.get("min_budget", 0),
        keywords=data.get("keywords") or [],
        exclusions=data.get("exclusions") or [],
        ai_prompt=data.get("ai_prompt"),
        ai_enabled=bool(data.get("ai_enabled")),
    )
    await session.commit()
    await callback.message.answer("✅ <b>Готово!</b> Новые подходящие заказы пришлю сюда.")
    await send_menu(callback.message, session, user)
    if scheduler is not None:
        scheduler.poll_soon()  # не ждём следующий цикл — проверим источники сразу


@router.callback_query(F.data == "cancel_setup")
async def cancel_setup(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("Настройка отменена. /menu — главное меню.")
    await callback.answer()


@router.message(StateFilter(ProfileSetup, HideSimilar, AddSource), ~F.text)
async def non_text_in_wizard(message: Message) -> None:
    await message.answer("Ответьте текстом или воспользуйтесь кнопками выше (или нажмите «Отмена»).")


# ------------------------------------------------------------------ menu


MY_SOURCES_TEXT = (
    "🌐 <b>Мои источники</b>\n\n"
    "Нажмите на источник, чтобы включить или выключить его.\n"
    "Можно добавить любой сайт с заказами — страницу со списком или RSS-ленту."
)


@router.callback_query(F.data == "my_sources")
async def my_sources(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_user(session, callback.from_user.id)
    profile = await get_user_profile(session, user.id) if user else None
    await callback.answer()
    if profile is None:
        await callback.message.answer(EMPTY_TEXT, reply_markup=empty_state_keyboard())
        return
    sources = await get_active_sources(session)
    await callback.message.answer(
        MY_SOURCES_TEXT, reply_markup=my_sources_keyboard(sources, [s.id for s in profile.sources])
    )


@router.callback_query(F.data.startswith("msrc:"))
async def toggle_my_source(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_user(session, callback.from_user.id)
    profile = await get_user_profile(session, user.id) if user else None
    if profile is None:
        await callback.answer("Сначала настройте профиль", show_alert=True)
        return
    source_id = int(callback.data.split(":", 1)[1])
    sources = await get_active_sources(session)
    if source_id not in {s.id for s in sources}:
        await callback.answer("Источник недоступен", show_alert=True)
        return
    if source_id in {s.id for s in profile.sources}:
        await unsubscribe_profile(session, profile.id, source_id)
        note = "Источник выключен"
    else:
        await subscribe_profile(session, profile.id, source_id)
        note = "Источник включён"
    await session.commit()
    profile = await get_user_profile(session, user.id)
    await callback.message.edit_reply_markup(
        reply_markup=my_sources_keyboard(sources, [s.id for s in profile.sources] if profile else [])
    )
    await callback.answer(note)


@router.callback_query(F.data == "back_menu")
async def back_menu(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user = await get_or_create_user(session, callback.from_user.id)
    await session.commit()
    await callback.answer()
    await send_menu(callback.message, session, user)


# ------------------------------------------------------------------ добавление своего источника

ADD_SOURCE_TEXT = (
    "➕ <b>Новый источник</b>\n\n"
    "Пришлите ссылку на страницу со списком заказов или на RSS-ленту.\n"
    "<i>Например: страница категории на бирже фриланса</i>\n\n"
    "<blockquote>Бот сам найдёт ленту или заказы на странице. Не подключаются сайты, которые "
    "запрещают автоматическое чтение (robots.txt) или закрывают доступ ботам.</blockquote>"
)


async def _back_to_wizard_sources(message: Message, state: FSMContext, session: AsyncSession) -> None:
    await state.set_state(ProfileSetup.waiting_for_sources)
    selected = (await state.get_data()).get("selected_sources", [])
    sources = await get_active_sources(session)
    await message.answer(
        step(2, "Источники") + "Где следить за заказами? Отметьте и нажмите «Готово».",
        reply_markup=sources_keyboard(sources, selected),
    )


@router.callback_query(F.data == "add_source")
async def add_source_start(callback: CallbackQuery, state: FSMContext) -> None:
    current = await state.get_state()
    back = "wizard" if current == ProfileSetup.waiting_for_sources.state else "menu"
    await state.update_data(add_source_return=back)
    await state.set_state(AddSource.waiting_for_url)
    await callback.answer()
    await callback.message.answer(ADD_SOURCE_TEXT, reply_markup=add_source_cancel_keyboard())


@router.message(AddSource.waiting_for_url, F.text)
async def add_source_url(
    message: Message, state: FSMContext, session: AsyncSession, scheduler: Any = None
) -> None:
    url = message.text.strip()
    wait = await message.answer("⏳ Проверяю сайт…")
    try:
        found = await check_source(url)
    except FeedCheckError as exc:
        await wait.edit_text(
            f"❌ {escape(str(exc))}\n\nПришлите другую ссылку или нажмите «Отмена».",
            reply_markup=add_source_cancel_keyboard(),
        )
        return
    except Exception:
        logger.exception("Feed check failed for %s", url)
        await wait.edit_text(
            "❌ Не получилось прочитать сайт. Пришлите другую ссылку или нажмите «Отмена».",
            reply_markup=add_source_cancel_keyboard(),
        )
        return

    from urllib.parse import urlsplit

    name = found.title or urlsplit(url).hostname or "Источник"
    await get_or_create_user(session, message.from_user.id)
    source = await create_feed_source(
        session, found.url, name, message.from_user.id, settings.poll_interval_seconds, kind=found.kind
    )
    await session.commit()
    if scheduler is not None:
        scheduler.poll_soon()

    done = (
        f"✅ Источник <b>{escape(source.name)}</b> добавлен.\n"
        + ("Нашла RSS-ленту. " if found.kind == "rss" else "Буду следить за страницей. ")
        + f"Сейчас там {len(found.items)} заказов — пришлю новые и самые свежие подходящие.\n"
        + "<i>Например: " + escape(found.items[0].title[:80]) + "</i>"
    )
    data = await state.get_data()
    if data.get("add_source_return") == "wizard":
        selected = list(data.get("selected_sources", []))
        if source.id not in selected:
            selected.append(source.id)
        await state.update_data(selected_sources=selected)
        await wait.edit_text(done)
        await _back_to_wizard_sources(message, state, session)
        return

    await state.clear()
    user = await get_user(session, message.from_user.id)
    profile = await get_user_profile(session, user.id) if user else None
    if profile is None:
        await wait.edit_text(done + "\n\nТеперь настройте профиль и отметьте этот источник.",
                             reply_markup=empty_state_keyboard())
        return
    await subscribe_profile(session, profile.id, source.id)
    await session.commit()
    await wait.edit_text(done + f"\nПрофиль «{escape(profile.name)}» уже подписан на него.")
    profile = await get_user_profile(session, user.id)
    sources = await get_active_sources(session)
    await message.answer(
        MY_SOURCES_TEXT, reply_markup=my_sources_keyboard(sources, [s.id for s in profile.sources] if profile else [])
    )


@router.callback_query(F.data == "cancel_add_source")
async def cancel_add_source(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    await callback.answer("Отменено")
    await callback.message.edit_reply_markup(reply_markup=None)
    if data.get("add_source_return") == "wizard":
        await _back_to_wizard_sources(callback.message, state, session)
    else:
        await state.clear()
        user = await get_or_create_user(session, callback.from_user.id)
        await session.commit()
        await send_menu(callback.message, session, user)


@router.callback_query(F.data == "recent_jobs")
async def recent_jobs(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_user(session, callback.from_user.id)
    profile = await get_user_profile(session, user.id) if user else None
    await callback.answer()
    if profile is None:
        await callback.message.answer(EMPTY_TEXT, reply_markup=empty_state_keyboard())
        return
    decisions = await get_recent_matches(session, profile.id, limit=5)
    if not decisions:
        stats = await get_decision_stats(session, profile.id)
        if not stats:
            text = "Пока ни один новый заказ не проверялся по этому профилю. Подождите цикл опроса (около минуты)."
        else:
            lines = [f"• {escape(k)}: {v}" for k, v in sorted(stats.items(), key=lambda kv: -kv[1])]
            text = (
                "Подходящих заказов пока нет. Что было проверено:\n" + "\n".join(lines)
                + "\n\nЕсли всё отсекается — ослабьте фильтры в «Настроить профиль» "
                "(меньше бюджет, больше ключевых слов или «Пропустить»)."
            )
        await callback.message.answer(text, parse_mode="HTML")
        return
    for d in reversed(decisions):
        await callback.message.answer(
            render_card(d, header="📋 Из последних подборок"),
            parse_mode="HTML",
            reply_markup=job_card_keyboard(d.job),
            disable_web_page_preview=True,
        )


@router.callback_query(F.data == "toggle_pause")
async def toggle_pause(callback: CallbackQuery, session: AsyncSession) -> None:
    user = await get_or_create_user(session, callback.from_user.id)
    user.notifications_paused = not user.notifications_paused
    await session.commit()
    await callback.answer()
    if user.notifications_paused:
        await callback.message.answer("⏸ Уведомления на паузе. Сбор данных продолжается.")
    else:
        await callback.message.answer("▶️ Уведомления снова включены.")
    await send_menu(callback.message, session, user)


# ------------------------------------------------------------------ «не показывать похожее»


@router.callback_query(F.data.startswith("hide:"))
async def hide_similar(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    try:
        job = await get_job(session, uuid.UUID(callback.data.split(":", 1)[1]))
    except ValueError:
        job = None
    await callback.answer()
    await state.set_state(HideSimilar.waiting_for_word)
    title = f" («{escape(job.title)}»)" if job else ""
    await callback.message.answer(
        f"Напишите слово, по которому отсекать такие заказы{title}.\n"
        "Оно добавится в исключения профиля. Например: логотип",
        parse_mode="HTML",
        reply_markup=cancel_keyboard(),
    )


@router.message(HideSimilar.waiting_for_word, F.text)
async def hide_similar_word(message: Message, state: FSMContext, session: AsyncSession) -> None:
    word = " ".join(message.text.split()).lower().lstrip("-").strip()
    if not 2 <= len(word) <= 64:
        await message.answer("Слово должно быть от 2 до 64 символов. Попробуйте ещё раз:")
        return
    user = await get_user(session, message.from_user.id)
    profile = await get_user_profile(session, user.id) if user else None
    await state.clear()
    if profile is None:
        await message.answer(EMPTY_TEXT, reply_markup=empty_state_keyboard())
        return
    await add_exclusion(session, profile.id, word)
    await session.commit()
    await message.answer(f"Готово: заказы со словом «{escape(word)}» больше не придут.", parse_mode="HTML")


# ------------------------------------------------------------------ удаление профиля


@router.message(Command("delete"))
async def cmd_delete(message: Message) -> None:
    await message.answer(
        "Удалить профиль, фильтры и историю подборок? Это нельзя отменить.",
        reply_markup=delete_confirm_keyboard(),
    )


@router.callback_query(F.data == "confirm_delete")
async def confirm_delete(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    await state.clear()
    user = await get_user(session, callback.from_user.id)
    if user is not None:
        await delete_user_data(session, user)
        await session.commit()
    await callback.message.edit_text("🗑 Профиль и настройки удалены. Чтобы начать заново — /start.")
    await callback.answer()


@router.callback_query(F.data == "cancel_delete")
async def cancel_delete(callback: CallbackQuery) -> None:
    await callback.message.edit_text("Удаление отменено.")
    await callback.answer()


# ------------------------------------------------------------------ всё остальное


@router.message()
async def unknown_message(message: Message) -> None:
    await message.answer("Не понял команду. Откройте /menu или /help.")


@router.callback_query()
async def stale_callback(callback: CallbackQuery) -> None:
    await callback.answer("Эта кнопка устарела. Откройте /menu", show_alert=False)
