from __future__ import annotations

import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from db.models import (
    User,
    UserStatus,
    Profile,
    FilterRule,
    RuleType,
    Source,
    SourceStatus,
    ProfileSource,
)
from bot.keyboards.main import get_main_menu_keyboard, get_sources_keyboard, get_confirmation_keyboard
from bot.states import ProfileSetup

logger = logging.getLogger(__name__)
router = Router()


async def get_active_sources(session: AsyncSession) -> list[Source]:
    result = await session.execute(
        select(Source).where(Source.status == SourceStatus.active)
    )
    return list(result.scalars().all())


async def show_source_selection(message: Message, state: FSMContext, session: AsyncSession) -> None:
    sources = await get_active_sources(session)
    if not sources:
        await state.update_data(selected_sources=[])
        await message.answer(
            "⚠️ Сейчас нет доступных источников. Пропускаем этот шаг.\n\n"
            "Шаг 3 из 5: Минимальный бюджет\n\n"
            "Введи минимальную сумму в рублях, например: 20000\n"
            "Или напиши \"пропустить\"."
        )
        await state.set_state(ProfileSetup.waiting_for_budget)
        return
    data = await state.get_data()
    selected = data.get("selected_sources", [])
    await message.answer(
        "📡 Выбери источники для отслеживания (можно несколько):",
        reply_markup=get_sources_keyboard(sources, selected)
    )
    await state.set_state(ProfileSetup.waiting_for_sources)


@router.message(Command("start"))
async def cmd_start(message: Message, session: AsyncSession) -> None:
    try:
        telegram_user_id = message.from_user.id
        result = await session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        user = result.scalar_one_or_none()
        if not user:
            user = User(telegram_user_id=telegram_user_id, status=UserStatus.active)
            session.add(user)
            await session.commit()
            logger.info(f"Created new user: {telegram_user_id}")
        await message.answer(
            "👋 Привет! Я FreelanceRadar — бот для мониторинга заказов.\n\nВыбери действие:",
            reply_markup=get_main_menu_keyboard()
        )
    except Exception as e:
        logger.exception("Error in /start: %s", e)
        await message.answer("⚠️ Произошла ошибка. Попробуйте позже.")


@router.message(Command("ping"))
async def cmd_ping(message: Message) -> None:
    await message.answer("pong")


@router.callback_query(F.data == "setup_profile")
async def start_profile_setup(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.message.edit_text(
        "🎯 Настройка профиля\n\nШаг 1 из 5: Название профиля\n\nВведи название, например:\n• Дизайн лендингов\n• Python-разработка"
    )
    await state.set_state(ProfileSetup.waiting_for_name)
    await callback.answer()


@router.message(ProfileSetup.waiting_for_name)
async def process_name(message: Message, state: FSMContext, session: AsyncSession) -> None:
    name = message.text.strip()
    if len(name) < 2 or len(name) > 120:
        await message.answer("❌ Название должно быть от 2 до 120 символов. Попробуй ещё раз:")
        return
    await state.update_data(profile_name=name)
    await show_source_selection(message, state, session)


@router.callback_query(ProfileSetup.waiting_for_sources, F.data.startswith("toggle_source:"))
async def toggle_source(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    source_id = int(callback.data.split(":")[1])
    data = await state.get_data()
    selected = data.get("selected_sources", [])
    if source_id in selected:
        selected.remove(source_id)
    else:
        selected.append(source_id)
    await state.update_data(selected_sources=selected)
    sources = await get_active_sources(session)
    await callback.message.edit_reply_markup(
        reply_markup=get_sources_keyboard(sources, selected)
    )
    await callback.answer()


@router.callback_query(ProfileSetup.waiting_for_sources, F.data == "sources_done")
async def sources_done(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    selected = data.get("selected_sources", [])
    if not selected:
        await callback.answer("Выбери хотя бы один источник", show_alert=True)
        return
    await callback.message.edit_text(
        f"✅ Источники: {len(selected)} выбрано\n\n"
        "Шаг 3 из 5: Минимальный бюджет\n\n"
        "Введи минимальную сумму в рублях, например: 20000\n"
        "Или напиши \"пропустить\"."
    )
    await state.set_state(ProfileSetup.waiting_for_budget)
    await callback.answer()


@router.message(ProfileSetup.waiting_for_budget)
async def process_budget(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if text.lower() in ["пропустить", "skip"]:
        budget = 0
    else:
        try:
            budget = int(text)
            if budget < 0 or budget > 10_000_000:
                raise ValueError
        except ValueError:
            await message.answer("❌ Бюджет должен быть целым числом от 0 до 10 000 000. Попробуй ещё раз:")
            return
    await state.update_data(min_budget=budget)
    await message.answer(
        f"✅ Минимальный бюджет: {budget:,} ₽\n\n"
        "Шаг 4 из 5: Ключевые слова\n\n"
        "Введи ключевые слова через запятую, например:\n"
        "лендинг, tilda, дизайн, верстка\n\n"
        "Максимум 20 слов."
    )
    await state.set_state(ProfileSetup.waiting_for_keywords)


@router.message(ProfileSetup.waiting_for_keywords)
async def process_keywords(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    keywords = [kw.strip().lower() for kw in text.split(",") if kw.strip()]
    if len(keywords) == 0:
        await message.answer("❌ Введи хотя бы одно ключевое слово:")
        return
    if len(keywords) > 20:
        await message.answer("❌ Слишком много слов (максимум 20). Сократи:")
        return
    for kw in keywords:
        if len(kw) < 2 or len(kw) > 64:
            await message.answer(f"❌ Слово '{kw}' должно быть от 2 до 64 символов. Попробуй ещё раз:")
            return
    await state.update_data(keywords=keywords)
    await message.answer(
        f"✅ Ключевые слова: {', '.join(keywords)}\n\n"
        "Шаг 5 из 5: Описание для ИИ (опционально)\n\n"
        "Кратко опиши, какие заказы ищешь, или напиши \"пропустить\".\n"
        "Максимум 1500 символов."
    )
    await state.set_state(ProfileSetup.waiting_for_ai_prompt)


@router.message(ProfileSetup.waiting_for_ai_prompt)
async def process_ai_prompt(message: Message, state: FSMContext, session: AsyncSession) -> None:
    text = message.text.strip()
    if text.lower() in ["пропустить", "skip"]:
        ai_prompt = None
        ai_enabled = False
    else:
        if len(text) > 1500:
            await message.answer("❌ Описание слишком длинное (максимум 1500 символов). Сократи:")
            return
        ai_prompt = text
        ai_enabled = True
    await state.update_data(ai_prompt=ai_prompt, ai_enabled=ai_enabled)
    data = await state.get_data()

    source_ids = data.get("selected_sources", [])
    source_names = []
    if source_ids:
        result = await session.execute(
            select(Source).where(Source.id.in_(source_ids))
        )
        source_names = [s.name for s in result.scalars().all()]

    summary = (
        "📋 Сводка профиля\n\n"
        f"Название: {data['profile_name']}\n"
        f"Источники: {', '.join(source_names) if source_names else 'не выбраны'}\n"
        f"Минимальный бюджет: {data['min_budget']:,} ₽\n"
        f"Ключевые слова: {', '.join(data['keywords'])}\n"
        f"ИИ-фильтр: {'включён' if data['ai_enabled'] else 'выключен'}\n"
    )
    if data["ai_prompt"]:
        summary += f"\nОписание для ИИ:\n{data['ai_prompt']}\n"
    summary += "\nВсё верно?"
    await message.answer(summary, reply_markup=get_confirmation_keyboard())
    await state.set_state(ProfileSetup.confirmation)


@router.callback_query(ProfileSetup.confirmation, F.data == "confirm_profile")
async def confirm_profile(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    data = await state.get_data()
    result = await session.execute(
        select(User).where(User.telegram_user_id == callback.from_user.id)
    )
    user = result.scalar_one()
    profile = Profile(
        user_id=user.id,
        name=data["profile_name"],
        min_budget=data["min_budget"],
        ai_prompt=data["ai_prompt"],
        ai_enabled=data["ai_enabled"],
        version=1
    )
    session.add(profile)
    await session.flush()
    for source_id in data.get("selected_sources", []):
        session.add(ProfileSource(profile_id=profile.id, source_id=source_id))
    for keyword in data["keywords"]:
        rule = FilterRule(
            profile_id=profile.id,
            rule_type=RuleType.keyword,
            value=keyword,
            is_exclusion=False
        )
        session.add(rule)
    await session.commit()
    await state.clear()
    await callback.message.edit_text(
        "✅ Готово! Профиль сохранён.\n\nТеперь я буду следить за новыми заказами."
    )
    await callback.answer()


@router.callback_query(F.data == "cancel_setup")
async def cancel_setup(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("❌ Настройка отменена.")
    await callback.answer()


@router.callback_query(F.data == "my_sources")
async def my_sources(callback: CallbackQuery, session: AsyncSession) -> None:
    result = await session.execute(
        select(User).where(User.telegram_user_id == callback.from_user.id)
    )
    user = result.scalar_one_or_none()
    if not user:
        await callback.message.answer("Сначала запустите /start")
        await callback.answer()
        return
    profiles = await session.execute(
        select(Profile).where(Profile.user_id == user.id)
    )
    profiles = profiles.scalars().all()
    if not profiles:
        await callback.message.answer("У вас пока нет профилей. Настройте профиль.")
        await callback.answer()
        return
    lines = []
    for profile in profiles:
        sources = await session.execute(
            select(Source).join(ProfileSource).where(ProfileSource.profile_id == profile.id)
        )
        sources = sources.scalars().all()
        source_names = ", ".join(s.name for s in sources) if sources else "не выбраны"
        lines.append(f"• {profile.name}: {source_names}")
    await callback.message.answer("📡 Мои источники:\n\n" + "\n".join(lines))
    await callback.answer()


@router.callback_query(F.data == "recent_jobs")
async def recent_jobs(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.message.answer(
        "📋 Последние подборки скоро появятся. Сначала настройте профиль и дождитесь новых заказов."
    )
    await callback.answer()


@router.callback_query(F.data == "toggle_pause")
async def toggle_pause(callback: CallbackQuery, session: AsyncSession) -> None:
    await callback.message.answer("⏸ Функция паузы пока в разработке.")
    await callback.answer()
