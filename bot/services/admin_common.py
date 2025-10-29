# services/admin_common.py
from aiogram.types import Message, CallbackQuery
from keyboards import kb_admin_menu
from db import is_admin, count_peers, count_access_requests, update_peer_stats
from services.wireguard import wg_dump_stats

async def is_admin_user(tg_id: int) -> bool:
    return await is_admin(tg_id)

async def render_admin_menu(evt: Message | CallbackQuery):
    stats = await wg_dump_stats()
    await update_peer_stats(stats)

    active  = await count_peers("active")
    revoked = await count_peers("revoked")
    pending = await count_access_requests("pending")

    text = "<b>🛠 Админ-панель</b>\nВыберите категорию:"
    kb = kb_admin_menu(active, pending, revoked)

    if isinstance(evt, Message):
        await evt.answer(text, reply_markup=kb)
    else:
        await evt.message.edit_text(text, reply_markup=kb)
