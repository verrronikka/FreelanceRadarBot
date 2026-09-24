from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_main_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(text="🎯 Настроить профиль", callback_data="setup_profile")],
        [InlineKeyboardButton(text="📡 Мои источники", callback_data="my_sources")],
        [InlineKeyboardButton(text="📋 Последние подборки", callback_data="recent_jobs")],
        [InlineKeyboardButton(text="⏸ Пауза уведомлений", callback_data="toggle_pause")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def get_sources_keyboard(sources, selected_ids):
    buttons = []
    for source in sources:
        mark = "✅" if source.id in selected_ids else "⬜"
        buttons.append([
            InlineKeyboardButton(
                text=f"{mark} {source.name}",
                callback_data=f"toggle_source:{source.id}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="Готово", callback_data="sources_done")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
