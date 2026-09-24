import logging
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from db.models import User, UserStatus, Profile, FilterRule, RuleType
from bot.keyboards.main import get_main_menu_keyboard
from bot.states import ProfileSetup

logger = logging.getLogger(__name__)
router = Router()


@router.message(Command("start"))
async def cmd_start(message: Message, session: AsyncSession):
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


@router.callback_query(F.data == "setup_profile")
async def start_profile_setup(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "🎯 Настройка профиля\n\nШаг 1 из 4: Название профиля\n\nВведи название, например:\n• Дизайн лендингов\n• Python-разработка"
    )
    await state.set_state(ProfileSetup.waiting_for_name)
    await callback.answer()


@router.message(ProfileSetup.waiting_for_name)
async def process_name(message: Message, state: FSMContext):
    name = message.text.strip()
    if len(name) < 2 or len(name) > 120:
        await message.answer("❌ Название должно быть от 2 до 120 символов. Попробуй ещё раз:")
        return
    await state.update_data(profile_name=name)
    await message.answer(
        "✅ Отлично!\n\nШаг 2 из 4: Минимальный бюджет\n\nВведи минимальную сумму в рублях, например: 20000\nИли напиши \"пропустить\"."
    )
    await state.set_state(ProfileSetup.waiting_for_budget)


@router.message(ProfileSetup.waiting_for_budget)
async def process_budget(message: Message, state: FSMContext):
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
        f"✅ Минимальный бюджет: {budget:,} ₽\n\nШаг 3 из 4: Ключевые слова\n\nВведи ключевые слова через запятую, например:\nлендинг, tilda, дизайн, верстка\n\nМаксимум 20 слов."
    )
    await state.set_state(ProfileSetup.waiting_for_keywords)


@router.message(ProfileSetup.waiting_for_keywords)
async def process_keywords(message: Message, state: FSMContext):
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
        f"✅ Ключевые слова: {', '.join(keywords)}\n\nШаг 4 из 4: Описание для ИИ (опционально)\n\nКратко опиши, какие заказы ищешь, или напиши \"пропустить\".\nМаксимум 1500 символов."
    )
    await state.set_state(ProfileSetup.waiting_for_ai_prompt)


@router.message(ProfileSetup.waiting_for_ai_prompt)
async def process_ai_prompt(message: Message, state: FSMContext):
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
    summary = (
        "📋 Сводка профиля\n\n"
        f"Название: {data['profile_name']}\n"
        f"Минимальный бюджет: {data['min_budget']:,} ₽\n"
        f"Ключевые слова: {', '.join(data['keywords'])}\n"
        f"ИИ-фильтр: {'включён' if data['ai_enabled'] else 'выключен'}\n"
    )
    if data["ai_prompt"]:
        summary += f"\nОписание для ИИ:\n{data['ai_prompt']}\n"
    summary += "\nВсё верно?"
    await message.answer(summary)
    await state.set_state(ProfileSetup.confirmation)


@router.callback_query(ProfileSetup.confirmation, F.data == "confirm_profile")
async def confirm_profile(callback: CallbackQuery, state: FSMContext, session: AsyncSession):
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
async def cancel_setup(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("❌ Настройка отменена.")
    await callback.answer()