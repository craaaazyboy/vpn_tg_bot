from typing import Tuple
from db import list_tickets_admin, count_tickets, get_ticket, count_ticket_users, list_ticket_users, count_tickets_by_user, list_tickets_by_user

PAGE_SIZE = 10
ADMIN_PAGE = 10

def status_human(s: str) -> str:
    return {
        "open": "Открыт",
        "pending_user": "Ждёт ответа пользователя",
        "answered": "Отвечен",
        "closed": "Закрыт",
    }.get(s, s)

async def render_admin_ticket_list(status: str, page: int) -> Tuple[str, list]:
    total = await count_tickets(status)
    total_pages = max(1, (total + PAGE_SIZE - 1)//PAGE_SIZE)
    rows = await list_tickets_admin(status, PAGE_SIZE, (page-1)*PAGE_SIZE)
    lines = [f"<b>🎫 Тикеты — {status_human(status)}</b>\nВсего: {total}"]
    return "\n".join(lines), rows, total_pages

async def render_ticket_text(ticket_id: int) -> Tuple[str, bool, bool]:
    t = await get_ticket(ticket_id)
    if not t:
        return "Тикет не найден.", False, True
    assignee_text = "—"
    if t.get("assignee"):
        if getattr(t["assignee"], "username", None):
            assignee_text = f"@{t['assignee'].username}"
        else:
            assignee_text = t["assignee"].first_name or "—"
    head = (f"<b>#{t['id']}</b> • <i>{t['subject']}</i>\n"
            f"Статус: <b>{status_human(t['status'])}</b>\n"
            f"Автор: <a href=\"tg://user?id={t['created_by']}\">{t['creator'].first_name or 'user'}</a>\n"
            f"Исполнитель: {assignee_text}")
    body = []
    for m in t["messages"]:
        who = "👮‍♂️" if m["sender_is_admin"] else "👤"
        body.append(f"{who} <i>{m['created_at']:%Y-%m-%d %H:%M}</i>\n{m['text']}")
    text = head + "\n\n" + "\n\n".join(body) if body else head
    return text, bool(t["assignee_id"]), (t["status"] == "closed")

def status_human_user(s: str) -> str:
    return {
        "open": "Открыт (ждёт ответа оператора)",
        "pending_user": "Есть ответ оператора (ждёт твоего ответа)",
        "answered": "Отвечен",
        "closed": "Закрыт",
    }.get(s, s)

async def render_user_ticket_text(ticket_id: int, viewer_id: int):
    t = await get_ticket(ticket_id)
    if not t or t["created_by"] != viewer_id:
        return "Тикет не найден.", True  # True => ошибка/закрыть кнопки
    head = f"<b>#{t['id']}</b> • <i>{t['subject']}</i>\nСтатус: <b>{status_human_user(t['status'])}</b>"
    body = []
    for m in t["messages"]:
        who = "👮‍♂️ Поддержка" if m["sender_is_admin"] else "👤 Ты"
        body.append(f"{who} • <i>{m['created_at']:%Y-%m-%d %H:%M}</i>\n{m['text']}")
    text = head + ("\n\n" + "\n\n".join(body) if body else "")
    is_closed = (t["status"] == "closed")
    return text, is_closed

async def render_admin_users_list(status: str | None, page: int):
    total = await count_ticket_users(status)
    total_pages = max(1, (total + ADMIN_PAGE - 1) // ADMIN_PAGE)
    rows = await list_ticket_users(status, ADMIN_PAGE, (page - 1) * ADMIN_PAGE)
    title = f"🎫 Пользователи с тикетами — {status_human(status)}" if status else "🎫 Пользователи с тикетами — все статусы"
    text = f"<b>{title}</b>\nВсего пользователей: {total}"
    return text, rows, total_pages

async def render_admin_user_tickets(user_id: int, status: str | None, page: int):
    total = await count_tickets_by_user(user_id, status)
    total_pages = max(1, (total + ADMIN_PAGE - 1) // ADMIN_PAGE)
    rows = await list_tickets_by_user(user_id, status, ADMIN_PAGE, (page - 1) * ADMIN_PAGE)
    text = f"<b>Тикеты пользователя</b> • uid={user_id}\nСтатус: {status_human(status) if status else 'все'}\nВсего: {total}"
    return text, rows, total_pages