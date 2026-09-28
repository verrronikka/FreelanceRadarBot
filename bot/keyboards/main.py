from __future__ import annotations

from typing import Iterable

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from db.models import Job, Source


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def main_menu_keyboard(paused: bool = False) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🎯 Настроить профиль", "setup_profile"), _btn("📡 Мои источники", "my_sources")],
            [
                _btn("📋 Последние подборки", "recent_jobs"),
                _btn("▶️ Возобновить" if paused else "⏸ Пауза уведомлений", "toggle_pause"),
            ],
        ]
    )


def empty_state_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("🎯 Настроить профиль", "setup_profile")]])


def sources_keyboard(sources: Iterable[Source], selected_ids: Iterable[int]) -> InlineKeyboardMarkup:
    selected = set(selected_ids)
    rows = [[_btn(f"{'✅' if s.id in selected else '⬜'} {s.name}", f"toggle_source:{s.id}")] for s in sources]
    rows.append([_btn("➕ Добавить свой источник", "add_source")])
    rows.append([_btn("Готово ➡️", "sources_done"), _btn("✖️ Отмена", "cancel_setup")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def my_sources_keyboard(sources: Iterable[Source], chosen_ids: Iterable[int]) -> InlineKeyboardMarkup:
    chosen = set(chosen_ids)
    rows = [[_btn(f"{'✅' if s.id in chosen else '⬜'} {s.name}", f"msrc:{s.id}")] for s in sources]
    rows.append([_btn("➕ Добавить источник", "add_source")])
    rows.append([_btn("⬅️ В меню", "back_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def add_source_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("✖️ Отмена", "cancel_add_source")]])


def skip_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("⏭ Пропустить", "skip_step"), _btn("✖️ Отмена", "cancel_setup")]])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[_btn("✖️ Отмена", "cancel_setup")]])


def confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("✅ Подтвердить", "confirm_profile"), _btn("❌ Отмена", "cancel_setup")]]
    )


def delete_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[_btn("🗑 Да, удалить", "confirm_delete"), _btn("Отмена", "cancel_delete")]]
    )


def job_card_keyboard(job: Job) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Откликнуться", url=job.url),
                _btn("🙈 Не показывать похожее", f"hide:{job.id}"),
            ]
        ]
    )
