from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

def kb_user_main(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="➕ Запросить VPN")],
        [KeyboardButton(text="📦 Мои устройства")],
        [KeyboardButton(text="ℹ️ Помощь")],
    ]
    if is_admin:
        rows.insert(0, [KeyboardButton(text="🛠 Админка")])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие…",
        one_time_keyboard=False
    )

def kb_admin_menu(active:int, pending:int, revoked:int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"Активные ({active})", callback_data="list:active:1")],
        [InlineKeyboardButton(text=f"Ожидают ({pending})", callback_data="list:pending:1")],
        [InlineKeyboardButton(text=f"Отключённые ({revoked})", callback_data="list:revoked:1")],
        [InlineKeyboardButton(text="🔎 Поиск", callback_data="admin:back")],  # хэндлер поиска — через /find
    ])
