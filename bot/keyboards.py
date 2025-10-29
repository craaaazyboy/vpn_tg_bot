from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

def kb_user_main(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="🆘 Поддержка")], 
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
        [InlineKeyboardButton(text="🎫 Тикеты (открытые)", callback_data="support:list:open:1")],
        [InlineKeyboardButton(text="👥 Тикеты по пользователям", callback_data="support:users:all:1")],
        [InlineKeyboardButton(text="🔎 Поиск", callback_data="admin:back")],  # хэндлер поиска — через /find
    ])

def kb_ticket_list_nav(page: int, total_pages: int, status: str):
    rows = []
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"support:list:{status}:{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"support:list:{status}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_ticket_row(ticket_id: int, subject: str):
    return [InlineKeyboardButton(text=f"#{ticket_id} • {subject}", callback_data=f"support:open:{ticket_id}")]

def kb_ticket_admin(ticket_id: int, is_assigned: bool, is_closed: bool):
    rows = []
    if not is_closed:
        rows.append([InlineKeyboardButton(text="✍️ Ответить", callback_data=f"support:reply:{ticket_id}")])
        if not is_assigned:
            rows.append([InlineKeyboardButton(text="✅ Взять", callback_data=f"support:assign:{ticket_id}")])
        rows.append([InlineKeyboardButton(text="🔒 Закрыть", callback_data=f"support:close:{ticket_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ К списку", callback_data="support:list:open:1")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_user_support_home(page: int, total_pages: int):
    rows = []
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"user:support:list:{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"user:support:list:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="➕ Новое обращение", callback_data="user:support:new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_user_ticket_row(ticket_id: int, subject: str):
    return [InlineKeyboardButton(text=f"#{ticket_id} • {subject}", callback_data=f"user:support:open:{ticket_id}")]

def kb_user_ticket_actions(ticket_id: int, is_closed: bool):
    rows = []
    if not is_closed:
        rows.append([InlineKeyboardButton(text="✍️ Ответить в тикете", callback_data=f"user:support:reply:{ticket_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ К списку", callback_data="user:support:list:1")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_ticket_users_nav(status: str | None, page: int, total_pages: int):
    st = status or "all"
    rows = []
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"support:users:{st}:{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"support:users:{st}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_ticket_user_row(uid: int, username: str | None, first_name: str | None, counts: dict, status: str | None):
    st = status or "all"
    title = username and f"@{username}" or (first_name or str(uid))
    badge = f"🟢{counts.get('open',0)}/🟡{counts.get('pending_user',0)}/🔵{counts.get('answered',0)}/⚫{counts.get('closed',0)}"
    return [InlineKeyboardButton(text=f"{title} • {badge}", callback_data=f"support:user:{uid}:{st}:1")]

def kb_ticket_user_tickets_nav(uid: int, status: str | None, page: int, total_pages: int):
    st = status or "all"
    rows = []
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"support:user:{uid}:{st}:{page-1}"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"support:user:{uid}:{st}:{page+1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="⬅️ К пользователям", callback_data=f"support:users:{st}:1")])
    return InlineKeyboardMarkup(inline_keyboard=rows)