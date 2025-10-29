# handlers/admin.py
from aiogram.fsm.context import FSMContext
from io import BytesIO
from typing import Optional
import logging, re

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, BufferedInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from state import AdminReply

from services.admin_common import is_admin_user, render_admin_menu
from services.admin_peers import (
    fetch_peers_page, build_peers_keyboard, open_peer_text_and_kb,
    do_revoke, resend_conf, render_online_text
)
from services.admin_requests import (
    render_pending_page, open_request_text, approve_request_flow,
    import_request_pairs_from_text, reject_request_flow
)
from services.support import render_admin_ticket_list, render_ticket_text, render_admin_users_list, render_admin_user_tickets, PAGE_SIZE
from keyboards import kb_ticket_list_nav, kb_ticket_row, kb_ticket_admin, kb_ticket_users_nav, kb_ticket_user_row, kb_ticket_user_tickets_nav

from db import is_admin as db_is_admin, assign_ticket, close_ticket, reply_ticket_from_admin, upsert_user

rt = Router()
PAGE_SIZE = 10

# ───────── Админ-меню ─────────

@rt.message(F.text, F.text.regexp(r"(?i)^\s*(?:🛠\s*)?админка\s*$"))
async def admin_menu_btn(m: Message):
    if not await is_admin_user(m.from_user.id):
        return
    await render_admin_menu(m)

@rt.message(Command("admin"))
async def admin_menu_cmd(m: Message):
    if not await is_admin_user(m.from_user.id):
        return
    await render_admin_menu(m)

@rt.callback_query(F.data == "admin:back")
async def admin_back(cb: CallbackQuery):
    if not await is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    await render_admin_menu(cb)
    await cb.answer()

# ───────── Списки пиров ─────────

@rt.callback_query(F.data.regexp(r"^list:(active|revoked):(\d+)$"))
async def list_peers(cb: CallbackQuery):
    if not await is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    _, status, page_s = cb.data.split(":")
    page = max(1, int(page_s))

    total, total_pages, rows = await fetch_peers_page(status, page)
    title = "Активные пиры" if status == "active" else "Отключённые пиры"
    if total == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")]])
        return await cb.message.edit_text(f"<b>{title}</b>\nПусто.", reply_markup=kb)

    kb = build_peers_keyboard(status, page, total_pages, rows)
    await cb.message.edit_text(f"<b>{title}</b>\nВсего: {total}", reply_markup=kb)
    await cb.answer()

# ───────── Карточка / revoke / resend ─────────

@rt.callback_query(F.data.startswith("peer:open:"))
async def open_peer(cb: CallbackQuery):
    if not await is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    peer_id = int(cb.data.split(":")[-1])
    text, kb = await open_peer_text_and_kb(peer_id)
    if not kb:
        return await cb.answer(text, show_alert=True)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()

@rt.callback_query(F.data.startswith("peer:revoke:"))
async def revoke_peer_cb(cb: CallbackQuery):
    if not await is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    peer_id = int(cb.data.split(":")[-1])
    try:
        msg = await do_revoke(peer_id, cb.from_user.id)
    except Exception as e:
        return await cb.answer(f"Ошибка при отключении: {e}", show_alert=True)
    await cb.answer(msg)
    await render_admin_menu(cb)

@rt.callback_query(F.data.startswith("peer:resend:"))
async def resend_conf_cb(cb: CallbackQuery):
    if not await is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    peer_id = int(cb.data.split(":")[-1])
    msg = await resend_conf(peer_id, cb.message.bot)
    await cb.answer(msg)

# ───────── Онлайн ─────────

@rt.message(Command("online"))
async def show_online_cmd(m: Message):
    if not await is_admin_user(m.from_user.id):
        return
    text = await render_online_text()
    await m.answer(text)

@rt.callback_query(lambda c: c.data == "list:connected")
async def show_online_cb(cb: CallbackQuery):
    if not await is_admin_user(cb.from_user.id):
        return
    text = await render_online_text()
    await cb.message.edit_text(text)
    await cb.answer()

# ───────── Pending заявки ─────────

@rt.callback_query(F.data.regexp(r"^list:pending:(\d+)$"))
async def list_pending(cb: CallbackQuery):
    if not await is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    page = int(cb.data.split(":")[2])
    text, kb = await render_pending_page(max(1, page))
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()

@rt.callback_query(F.data.startswith("req:open:"))
async def open_request(cb: CallbackQuery):
    if not await is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    req_id = int(cb.data.split(":")[-1])
    text, kb = await open_request_text(req_id)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()

@rt.callback_query(F.data.startswith("req:approve:"))
async def approve_request(cb: CallbackQuery):
    if not await is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)

    parts = cb.data.split(":")
    req_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 1
    actor_id = cb.from_user.id

    # гарантируем, что админ присутствует в users (FK decided_by -> users.tg_id)
    from db import upsert_user
    await upsert_user(
        actor_id,
        cb.from_user.username,
        cb.from_user.first_name,
        cb.from_user.last_name,
    )

    try:
        msg, _ = await approve_request_flow(req_id, page, cb.message.bot, actor_id=actor_id)
    except Exception:
        import logging
        logging.exception("approve_request_flow failed")
        return await cb.answer("Ошибка создания peer (см. логи).", show_alert=True)

    await cb.answer(msg)
    text, kb = await render_pending_page(page)
    await cb.message.edit_text(text, reply_markup=kb)


@rt.callback_query(F.data.startswith("req:reject:"))
async def reject_request(cb: CallbackQuery):
    if not await is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)

    parts = cb.data.split(":")
    req_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 1
    actor_id = cb.from_user.id

    from db import get_access_request
    r = await get_access_request(req_id)
    if not r or r["status"] != "pending":
        await cb.answer("Заявка уже обработана.", show_alert=True)
    else:
        try:
            msg = await reject_request_flow(r, actor_id, cb.message.bot)
        except Exception:
            import logging
            logging.exception("reject_request_flow failed")
            msg = "Ошибка при отклонении (см. логи)."
        await cb.answer(msg)

    text, kb = await render_pending_page(page)
    await cb.message.edit_text(text, reply_markup=kb)

# ───────── Импорт заявок/привязок из файла ─────────

@rt.message(F.document)
async def import_requests_from_file(m: Message):
    if not await is_admin_user(m.from_user.id):
        return
    doc = m.document
    filename = (doc.file_name or "").lower()
    if not (filename.endswith(".html") or filename.endswith(".json") or filename.endswith(".txt")):
        return
    buf = BytesIO()
    await m.bot.download(doc, destination=buf)
    content = buf.getvalue().decode("utf-8", "ignore")

    linked, skipped = await import_request_pairs_from_text(content)
    await m.reply(f"Готово! 🔗 {linked} привязано • ⏭️ {skipped} пропущено.")

@rt.callback_query(F.data.regexp(r"^support:list:(open|pending_user|answered|closed):(\d+)$"))
async def support_list(cb: CallbackQuery):
    if not await db_is_admin(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    _, _, status, page_s = cb.data.split(":")
    page = max(1, int(page_s))

    text, rows, total_pages = await render_admin_ticket_list(status, page)
    kb_rows = [kb_ticket_row(r["id"], r["subject"]) for r in rows]
    kb = kb_ticket_list_nav(page, total_pages, status)
    kb.inline_keyboard = kb_rows + kb.inline_keyboard  # prepend rows
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()

# ——— Открыть тикет ———
@rt.callback_query(F.data.startswith("support:open:"))
async def support_open(cb: CallbackQuery):
    if not await db_is_admin(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    ticket_id = int(cb.data.split(":")[-1])
    text, is_assigned, is_closed = await render_ticket_text(ticket_id)
    kb = kb_ticket_admin(ticket_id, is_assigned, is_closed)
    await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
    await cb.answer()

# ——— Взять тикет ———
@rt.callback_query(F.data.startswith("support:assign:"))
async def support_assign(cb: CallbackQuery):
    if not await db_is_admin(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)

    # ensure admin exists in users (FK)
    await upsert_user(cb.from_user.id, cb.from_user.username, cb.from_user.first_name, cb.from_user.last_name)

    ticket_id = int(cb.data.split(":")[-1])
    ok = await assign_ticket(ticket_id, cb.from_user.id)
    await cb.answer("Тикет принят в работу ✅" if ok else "Не удалось взять тикет", show_alert=not ok)
    await support_open(cb)

# ——— Закрыть тикет ———
@rt.callback_query(F.data.startswith("support:close:"))
async def support_close(cb: CallbackQuery):
    if not await db_is_admin(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    ticket_id = int(cb.data.split(":")[-1])
    ok = await close_ticket(ticket_id, cb.from_user.id)
    await cb.answer("Тикет закрыт ✅" if ok else "Не удалось закрыть", show_alert=not ok)
    await support_open(cb)

# ——— Ответить ———
@rt.callback_query(F.data.startswith("support:reply:"))
async def support_reply_prompt(cb: CallbackQuery, state: FSMContext):
    if not await db_is_admin(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    ticket_id = int(cb.data.split(":")[-1])
    await state.set_state(AdminReply.waiting_text)
    await state.update_data(ticket_id=ticket_id)
    await cb.message.answer(f"Напишите ответ для тикета #{ticket_id}:")
    await cb.answer()

@rt.message(AdminReply.waiting_text, F.text.len() > 0)
async def support_reply_send(m: Message, state: FSMContext):
    if not await db_is_admin(m.from_user.id):
        return
    data = await state.get_data()
    ticket_id = int(data["ticket_id"])
    user_to_notify = await reply_ticket_from_admin(ticket_id, m.from_user.id, m.text.strip())
    await state.clear()
    await m.answer(f"Ответ отправлен в тикет #{ticket_id} ✅")

    # уведомить автора
    if user_to_notify:
        try:
            await m.bot.send_message(
                user_to_notify,
                f"🔔 Ответ в тикете #{ticket_id}:\n\n{m.text.strip()}\n\nОткрой тикет из меню «🆘 Поддержка» или командой /tickets.",
            )
        except Exception:
            pass

# Входная точка: список пользователей по статусу
# Примеры коллбеков:
#   support:users:all:1
#   support:users:open:1
#   support:users:pending_user:1
#   support:users:answered:1
#   support:users:closed:1
@rt.callback_query(F.data.regexp(r"^support:users:(all|open|pending_user|answered|closed):(\d+)$"))
async def support_users(cb: CallbackQuery):
    if not await db_is_admin(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)

    parts = cb.data.split(":")
    st = parts[2]
    page = max(1, int(parts[3]))
    status = None if st == "all" else st

    text, rows, total_pages = await render_admin_users_list(status, page)

    # строим список кнопок-юзеров
    kb_rows = []
    for r in rows:
        counts = {
            "open": r.get("open", 0),
            "pending_user": r.get("pending_user", 0),
            "answered": r.get("answered", 0),
            "closed": r.get("closed", 0),
        }
        kb_rows.append(kb_ticket_user_row(
            uid=r["uid"],
            username=r.get("username"),
            first_name=r.get("first_name"),
            counts=counts,
            status=status
        ))

    kb = kb_ticket_users_nav(status, page, total_pages)
    kb.inline_keyboard = kb_rows + kb.inline_keyboard
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()

# Тикеты конкретного пользователя
# support:user:<uid>:<status|all>:<page>
@rt.callback_query(F.data.regexp(r"^support:user:(\d+):(all|open|pending_user|answered|closed):(\d+)$"))
async def support_user_tickets(cb: CallbackQuery):
    if not await db_is_admin(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)

    _, _, uid_s, st, page_s = cb.data.split(":")
    uid = int(uid_s)
    status = None if st == "all" else st
    page = max(1, int(page_s))

    text, rows, total_pages = await render_admin_user_tickets(uid, status, page)

    # список тикетов пользователя
    kb_rows = [kb_ticket_row(r["id"], r["subject"]) for r in rows]
    kb = kb_ticket_user_tickets_nav(uid, status, page, total_pages)
    kb.inline_keyboard = kb_rows + kb.inline_keyboard
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()