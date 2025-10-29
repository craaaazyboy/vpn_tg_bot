# services/admin_requests.py
from typing import Tuple, List, Dict, Any
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from presenters.admin_ui import kb_pending_list, kb_back_to_pending, render_request_card
from db import (
    list_access_requests, count_access_requests, get_access_request, decide_access_request,
    insert_peer, upsert_user, link_peer_owner
)
from services.wireguard import add_peer, fetch_client_conf_and_qr, list_peers_from_wgconf
from presenters.admin_ui import kb_pending_row
from utils.req_parse import extract_pairs_from_text

PAGE_SIZE = 10

async def render_pending_page(page: int) -> Tuple[str, InlineKeyboardMarkup]:
    total = await count_access_requests("pending")
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    if page > total_pages: page = total_pages
    offset = (page - 1) * PAGE_SIZE
    rows = await list_access_requests("pending", PAGE_SIZE, offset)

    if total == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")]])
        return "<b>Ожидают решения</b>\nПока пусто.", kb

    buttons: List[List[InlineKeyboardButton]] = []
    for r in rows:
        title = f"{r['username'] or r['first_name'] or r['tg_user']} — {r['device_name']}  (#{r['id']})"
        buttons.append([InlineKeyboardButton(text=title, callback_data=f"req:open:{r['id']}")])
        buttons.append([*kb_pending_row(r['id'], page)])

    nav = kb_pending_list(page, total_pages)
    if nav:
        buttons.append(nav)
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])

    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    text = f"<b>Ожидают решения</b>\nВсего: {total}"
    return text, kb

async def open_request_text(req_id: int) -> Tuple[str, InlineKeyboardMarkup]:
    r = await get_access_request(req_id)
    if not r or r["status"] != "pending":
        return "Заявка не найдена или уже обработана.", kb_back_to_pending(1)
    text = render_request_card(r)
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"req:approve:{req_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"req:reject:{req_id}")
    ],[
        InlineKeyboardButton(text="⬅️ Назад", callback_data="list:pending:1")
    ]])
    return text, kb

async def approve_request_flow(req_id: int, page: int, bot, actor_id: int) -> Tuple[str, int]:
    r = await get_access_request(req_id)
    if not r or r["status"] != "pending":
        return "Заявка уже обработана.", page

    tg_user = r["tg_user"]
    device_name = r["device_name"]

    # создаём peer на хосте
    pubkey, ip_oct, cidr, conf_path = await add_peer(device_name)

    # фиксируем peer в БД
    await insert_peer(
        tg_user=tg_user,
        device_name=device_name,
        pubkey=pubkey,
        ip_oct=ip_oct,
        allowed_cidr=cidr,
        conf_path=conf_path,
        api_client_id=None,
    )

    # помечаем заявку решённой от имени реального актёра
    await decide_access_request(req_id, actor_id, "approved")

    # отправляем конфиг пользователю
    conf_text, qr_png = await fetch_client_conf_and_qr(conf_path)
    await bot.send_photo(
        tg_user,
        photo=BufferedInputFile(qr_png, filename=f"{device_name}.png"),
        caption=f"Туннель <b>{device_name}</b>. Сканируйте QR в WireGuard."
    )
    await bot.send_document(
        tg_user,
        document=BufferedInputFile(conf_text.encode(), filename=f"{device_name}.conf")
    )

    return "Одобрено ✅", page


async def reject_request_flow(r: Dict[str, Any], actor_id: int, bot) -> str:
    await decide_access_request(r["id"], actor_id, "rejected")
    try:
        await bot.send_message(
            r["tg_user"],
            f"Заявка на устройство <code>{r['device_name']}</code> отклонена.",
        )
    except Exception:
        import logging
        logging.info("notify user on reject failed", exc_info=True)
    return "Отклонено ❌"

# импорт заявок/привязок по файлу
async def import_request_pairs_from_text(content: str) -> Tuple[int, int]:
    linked = skipped = 0
    seen: set[int] = set()
    pairs = extract_pairs_from_text(content)
    for tg_id, device_name in pairs:
        if tg_id not in seen:
            await upsert_user(tg_id, None, None, None)
            seen.add(tg_id)
        if await link_peer_owner(device_name, tg_id):
            linked += 1
        else:
            skipped += 1
    return linked, skipped
