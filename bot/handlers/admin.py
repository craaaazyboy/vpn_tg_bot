from io import BytesIO
from typing import Optional, Tuple, List
import logging, re

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery, BufferedInputFile,
    InlineKeyboardMarkup, InlineKeyboardButton
)

from keyboards import kb_admin_menu
from utils.format import fmt_bytes, fmt_owner
from db import (
    is_admin, update_peer_stats, revoke_peer,
    upsert_user, link_peer_owner, link_peer_owner_by_id,
    upsert_peer_skeleton, owners_by_pubkeys, find_candidates_by_device,
    get_peer_by_id, list_peers_like,
    # НОВОЕ:
    count_peers, count_access_requests, list_peers_page,
    get_access_request, insert_peer, decide_access_request
)
from services.wireguard import (
    wg_dump_stats, fetch_client_conf_and_qr, list_peers_from_wgconf,
    add_peer, ssh_revoke_by_device_or_pubkey
)

rt = Router()
PAGE_SIZE = 10

# ───────── helpers ─────────

async def _is_admin_user(tg_id: int) -> bool:
    return await is_admin(tg_id)

def _kb_peer_card_safe(tg_user: Optional[int], peer_id: int) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if tg_user:
        rows.append([InlineKeyboardButton(text="💬 Написать", url=f"tg://user?id={tg_user}")])
    rows.append([
        InlineKeyboardButton(text="🔁 Отправить конфиг", callback_data=f"peer:resend:{peer_id}"),
        InlineKeyboardButton(text="⛔ Отключить",       callback_data=f"peer:revoke:{peer_id}"),
    ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def _render_admin_menu(m: Message | CallbackQuery):
    stats = await wg_dump_stats()
    await update_peer_stats(stats)

    active  = await count_peers("active")
    revoked = await count_peers("revoked")
    pending = await count_access_requests("pending")

    text = "<b>🛠 Админ-панель</b>\nВыберите категорию:"
    kb = kb_admin_menu(active, pending, revoked)

    if isinstance(m, Message):
        await m.answer(text, reply_markup=kb)
    else:
        await m.message.edit_text(text, reply_markup=kb)

def _kb_list_peers(status: str, page: int, total_pages: int, rows: list[dict]) -> InlineKeyboardMarkup:
    buttons: List[List[InlineKeyboardButton]] = []

    for r in rows:
        owner = fmt_owner(r.get("tg_id"), r.get("username"), r.get("first_name"), r.get("last_name"))
        title = f"{owner} — {r['device_name']} • {r['allowed_cidr']}"
        buttons.append([InlineKeyboardButton(text=title, callback_data=f"peer:open:{r['id']}")])

    nav: List[InlineKeyboardButton] = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"list:{status}:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages or 1}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"list:{status}:{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def _fetch_peers_page(status: str, page: int):
    assert status in ("active", "revoked")
    offset = (page - 1) * PAGE_SIZE

    total = await count_peers(status)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 1
    rows = await list_peers_page(status, PAGE_SIZE, offset)

    return total, total_pages, rows

# ───────── админ-меню ─────────

@rt.message(F.text, F.text.regexp(r"(?i)^\s*(?:🛠\s*)?админка\s*$"))
async def admin_menu_btn(m: Message):
    if not await _is_admin_user(m.from_user.id):
        return
    await _render_admin_menu(m)

@rt.message(Command("admin"))
async def admin_menu_cmd(m: Message):
    if not await _is_admin_user(m.from_user.id):
        return
    await _render_admin_menu(m)

# ───────── списки пиров ─────────

@rt.callback_query(F.data.regexp(r"^list:(active|revoked):(\d+)$"))
async def list_peers(cb: CallbackQuery):
    if not await _is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)

    _, status, page_s = cb.data.split(":")
    page = max(1, int(page_s))

    total, total_pages, rows = await _fetch_peers_page(status, page)
    title = "Активные пиры" if status == "active" else "Отключённые пиры"

    if total == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")]
        ])
        return await cb.message.edit_text(f"<b>{title}</b>\nПусто.", reply_markup=kb)

    kb = _kb_list_peers(status, page, total_pages, rows)
    await cb.message.edit_text(f"<b>{title}</b>\nВсего: {total}", reply_markup=kb)
    await cb.answer()

# ───────── ожидающие заявки ─────────

@rt.callback_query(F.data.regexp(r"^list:pending:(\d+)$"))
async def list_pending(cb: CallbackQuery):
    if not await _is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    page = int(cb.data.split(":")[2])
    await _render_pending(cb, page)
    await cb.answer()


@rt.callback_query(F.data == "admin:back")
async def admin_back(cb: CallbackQuery):
    if not await _is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)
    await _render_admin_menu(cb)
    await cb.answer()

# ───────── карточка пира ─────────

@rt.callback_query(F.data.startswith("peer:open:"))
async def open_peer(cb: CallbackQuery):
    if not await _is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)

    peer_id = int(cb.data.split(":")[-1])
    row = await get_peer_by_id(peer_id)
    if not row:
        return await cb.answer("Пир не найден", show_alert=True)

    pubkey = row["peer_pubkey"]
    rx = row["rx_bytes"]; tx = row["tx_bytes"]; last = row["last_handshake"]

    try:
        stats = await wg_dump_stats()
        s = next((x for x in stats if x["pubkey"] == pubkey), None)
        if s:
            rx = s["rx_bytes"]; tx = s["tx_bytes"]; last = s["last_handshake"]
    except Exception:
        pass

    owner = fmt_owner(row["tg_user"], row.get("username"), row.get("first_name"), row.get("last_name"))
    last_hs = last.strftime("%Y-%m-%d %H:%M:%S") if last else "—"

    text = (
        f"<b>{row['device_name']}</b>\n"
        f"👤 {owner}\n"
        f"🔑 <code>{pubkey}</code>\n"
        f"🌐 {row['allowed_cidr']}\n"
        f"⏱ Last HS: {last_hs}\n"
        f"📥 RX: {fmt_bytes(rx)}    📤 TX: {fmt_bytes(tx)}"
    )
    await cb.message.edit_text(text, reply_markup=_kb_peer_card_safe(row["tg_user"], row["id"]))
    await cb.answer()

# ───────── отключить ─────────

@rt.callback_query(F.data.startswith("peer:revoke:"))
async def do_revoke(cb: CallbackQuery):
    if not await _is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)

    peer_id = int(cb.data.split(":")[-1])
    row = await get_peer_by_id(peer_id)
    if not row:
        return await cb.answer("Уже отключён или не найден.", show_alert=True)

    try:
        await ssh_revoke_by_device_or_pubkey(device_name=row["device_name"], pubkey=row["peer_pubkey"])
        await revoke_peer(peer_id, cb.from_user.id)
    except Exception as e:
        return await cb.answer(f"Ошибка при отключении: {e}", show_alert=True)

    await cb.answer("Пир отключён ✅")
    await _render_admin_menu(cb)

# ───────── повторная отправка конфига ─────────

@rt.callback_query(F.data.startswith("peer:resend:"))
async def resend_conf(cb: CallbackQuery):
    if not await _is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)

    peer_id = int(cb.data.split(":")[-1])
    row = await get_peer_by_id(peer_id)
    if not row or not row["tg_user"] or not row["conf_path"]:
        return await cb.answer("Конфиг не найден или у пира нет владельца.", show_alert=True)

    conf_text, qr_png = await fetch_client_conf_and_qr(row["conf_path"])
    await cb.message.bot.send_photo(
        row["tg_user"],
        photo=qr_png,
        caption=f"Туннель <b>{row['device_name']}</b>. Сканируйте QR в WireGuard."
    )
    await cb.message.bot.send_document(
        row["tg_user"],
        document=BufferedInputFile(conf_text.encode(), filename=f"{row['device_name']}.conf")
    )
    await cb.answer("Конфиг отправлен ✅")

# ───────── импорт пиров из wg0.conf ─────────

@rt.message(Command("import_peers"))
async def import_peers_cmd(m: Message):
    if not await _is_admin_user(m.from_user.id):
        return
    items = await list_peers_from_wgconf()
    created = updated = skipped = 0
    for it in items:
        _, status = await upsert_peer_skeleton(
            device_name=it["device_name"],
            public_key=it["public_key"],
            allowed_cidr=it.get("allowed_cidr")
        )
        if status == "created": created += 1
        elif status == "updated": updated += 1
        else: skipped += 1
    await m.answer(f"Импорт: всего {len(items)} • создано {created}, обновлено {updated}, пропущено {skipped} (владельцы не тронут).")

# ───────── импорт заявок/привязок из файла чата ─────────

_RE_LINK = re.compile(r'<a\s+href="tg://user\?id=(\d+)">([^<]+)</a>')
_RE_TGID_HTML = re.compile(r'tg://user\?id=(\d+)')
_RE_CODE_HTML = re.compile(r'<code>([^<]+)</code>')
_RE_BACKTICKS = re.compile(r'`([^`]+)`')

def _split_personal_name(full: str) -> Tuple[Optional[str], Optional[str]]:
    full = (full or "").strip()
    if not full:
        return None, None
    parts = full.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]

def _extract_tg_and_display_name(text: str) -> Tuple[Optional[int], Optional[str]]:
    m = _RE_LINK.search(text)
    if m:
        return int(m.group(1)), m.group(2).strip()
    m2 = _RE_TGID_HTML.search(text)
    if m2:
        return int(m2.group(1)), None
    return None, None

def _extract_device_name(text: str) -> Optional[str]:
    m = _RE_CODE_HTML.search(text)
    if m:
        return m.group(1).strip()
    m = _RE_BACKTICKS.search(text)
    if m:
        return m.group(1).strip()
    blocks = re.split(r'Запрос VPN', text, flags=re.IGNORECASE)
    for b in blocks[1:] or blocks:
        for line in b.splitlines():
            line = re.sub(r'<.*?>', '', line).strip()
            if not line:
                continue
            if line.startswith("💻"):
                return line.lstrip("💻").strip()
    return None

def _kb_bind_candidates(tg_id: int, guess: str, rows: list[dict]) -> InlineKeyboardMarkup:
    kb = []
    for r in rows:
        title = f"{r['device_name']} • {r['allowed_cidr']}"
        kb.append([InlineKeyboardButton(text=title, callback_data=f"bind:{r['id']}:{tg_id}")])
    kb.append([InlineKeyboardButton(text="Отмена", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

@rt.message(Command("import_requests_help"))
async def import_requests_help(m: Message):
    if not await _is_admin_user(m.from_user.id):
        return
    await m.answer(
        "📥 Пришлите файл экспорта чата (HTML/JSON/TXT) c карточками «Запрос VPN».\n"
        "Бот найдёт пары <tg_id, device> и привяжет устройства к пользователям (существующих не перезаписывает)."
    )

def _extract_pairs_from_text(txt: str) -> list[tuple[int, str]]:
    pairs: list[tuple[int, str]] = []
    blocks = re.split(r'Запрос VPN', txt)
    for b in blocks:
        m1 = _RE_TGID_HTML.search(b)
        m2 = _RE_CODE_HTML.search(b)
        if m1 and m2:
            tg_id = int(m1.group(1))
            device = m2.group(1).strip()
            pairs.append((tg_id, device))
    return pairs

@rt.message(F.document)
async def import_requests_from_file(m: Message):
    if not await _is_admin_user(m.from_user.id):
        return
    doc = m.document
    filename = (doc.file_name or "").lower()
    if not (filename.endswith(".html") or filename.endswith(".json") or filename.endswith(".txt")):
        return

    buf = BytesIO()
    await m.bot.download(doc, destination=buf)
    content = buf.getvalue().decode("utf-8", "ignore")

    pairs = _extract_pairs_from_text(content)
    if not pairs:
        return await m.reply("Не нашёл карточек «Запрос VPN» в файле.")

    linked = skipped = 0
    seen_users: set[int] = set()

    for tg_id, device_name in pairs:
        if tg_id not in seen_users:
            await upsert_user(tg_id, None, None, None)
            seen_users.add(tg_id)
        if await link_peer_owner(device_name, tg_id):
            linked += 1
        else:
            skipped += 1

    await m.reply(f"Готово! 🔗 {linked} привязано • ⏭️ {skipped} пропущено.")

# ───────── быстрый поиск ─────────

@rt.message(Command("find"))
async def admin_find(m: Message):
    if not await _is_admin_user(m.from_user.id):
        return
    parts = m.text.split(maxsplit=1)
    q = parts[1] if len(parts) > 1 else ""
    if not q:
        return await m.answer("Использование: /find <часть имени устройства>")

    rows = await list_peers_like(q)
    if not rows:
        return await m.answer("Ничего не нашёл.")
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"{fmt_owner(r.get('tg_id'), r.get('username'), r.get('first_name'), r.get('last_name'))} — {r['device_name']} • {r['allowed_cidr']}",
            callback_data=f"peer:open:{r['id']}"
        )] for r in rows
    ] + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")]] )
    await m.answer("Результаты:", reply_markup=kb)

# ───────── кто онлайн ─────────

@rt.message(Command("online"))
async def show_online_cmd(m: Message):
    if not await _is_admin_user(m.from_user.id):
        return
    await _render_online(m)

@rt.callback_query(lambda c: c.data == "list:connected")
async def show_online_cb(cb: CallbackQuery):
    if not await _is_admin_user(cb.from_user.id):
        return
    await _render_online(cb)

async def _render_online(evt: Message | CallbackQuery):
    stats = await wg_dump_stats()
    online = [s for s in stats if s.get("last_handshake")]

    pubkeys = [s["pubkey"] for s in online]
    meta = await owners_by_pubkeys(pubkeys)

    lines = []
    for s in sorted(online, key=lambda x: x["last_handshake"], reverse=True):
        m = meta.get(s["pubkey"], {})
        owner = fmt_owner(m.get("tg_id"), m.get("username"), m.get("first_name"), m.get("last_name"))
        dev   = (m.get("device_name") or "?").strip()
        rx_h, tx_h = fmt_bytes(s["rx_bytes"]), fmt_bytes(s["tx_bytes"])
        lines.append(f"• {owner} — <code>{dev}</code>  📥 {rx_h} / 📤 {tx_h}")

    text = "🟢 <b>Сейчас онлайн</b>\n" + ("\n".join(lines) if lines else "Никто не подключён.")
    if isinstance(evt, Message):
        await evt.answer(text)
    else:
        await evt.message.edit_text(text)
        await evt.answer()

# ───────── заявки: карточка/approve/reject ─────────

@rt.callback_query(F.data.startswith("req:open:"))
async def open_request(cb: CallbackQuery):
    if not await _is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)

    req_id = int(cb.data.split(":")[-1])
    r = await get_access_request(req_id)
    if not r or r["status"] != "pending":
        return await cb.answer("Заявка не найдена или уже обработана.", show_alert=True)

    owner = fmt_owner(r["tg_user"], r["username"], r["first_name"], r["last_name"])
    text = (
        f"<b>Заявка #{req_id}</b>\n"
        f"👤 {owner} (id: <code>{r['tg_user']}</code>)\n"
        f"💻 <code>{r['device_name']}</code>\n"
        f"Статус: pending"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"req:approve:{req_id}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"req:reject:{req_id}")
    ],[
        InlineKeyboardButton(text="⬅️ Назад", callback_data="list:pending:1")
    ]])
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()

@rt.callback_query(F.data.startswith("req:approve:"))
async def approve_request(cb: CallbackQuery):
    if not await _is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)

    parts = cb.data.split(":")
    req_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 1

    r = await get_access_request(req_id)
    if not r or r["status"] != "pending":
        await cb.answer("Заявка уже обработана.", show_alert=True)
        return await _render_pending(cb, page)

    tg_user = r["tg_user"]
    device_name = r["device_name"]

    try:
        pubkey, ip_oct, cidr, conf_path = await add_peer(device_name)
    except Exception as e:
        logging.exception("approve_request: add_peer failed")
        return await cb.answer(f"Ошибка создания peer: {e}", show_alert=True)

    await insert_peer(
        tg_user=tg_user,
        device_name=device_name,
        pubkey=pubkey,
        ip_oct=ip_oct,
        allowed_cidr=cidr,
        conf_path=conf_path,
        api_client_id=None
    )
    await decide_access_request(req_id, cb.from_user.id, "approved")

    conf_text, qr_png = await fetch_client_conf_and_qr(conf_path)
    await cb.message.bot.send_photo(
        tg_user,
        photo=qr_png,
        caption=f"Туннель <b>{device_name}</b>. Сканируйте QR в WireGuard."
    )
    await cb.message.bot.send_document(
        tg_user,
        document=BufferedInputFile(conf_text.encode(), filename=f"{device_name}.conf")
    )

    await cb.answer("Одобрено ✅")
    await _render_pending(cb, page)

@rt.callback_query(F.data.startswith("req:reject:"))
async def reject_request(cb: CallbackQuery):
    if not await _is_admin_user(cb.from_user.id):
        return await cb.answer("Нет прав", show_alert=True)

    parts = cb.data.split(":")
    req_id = int(parts[2])
    page = int(parts[3]) if len(parts) > 3 else 1

    r = await get_access_request(req_id)
    if not r or r["status"] != "pending":
        await cb.answer("Заявка уже обработана.", show_alert=True)
        return await _render_pending(cb, page)

    await decide_access_request(req_id, cb.from_user.id, "rejected")

    try:
        await cb.message.bot.send_message(
            r["tg_user"],
            f"Заявка на устройство <code>{r['device_name']}</code> отклонена."
        )
    except Exception:
        pass

    await cb.answer("Отклонено ❌")
    await _render_pending(cb, page)


async def _render_pending(evt: Message | CallbackQuery, page: int):
    from db import list_access_requests, count_access_requests  # локально, чтобы не плодить импорты

    if page < 1:
        page = 1

    total = await count_access_requests("pending")
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    # если текущее число страниц уменьшилось (например, мы только что одобрили заявку) — подвинемся влево
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * PAGE_SIZE
    rows = await list_access_requests("pending", PAGE_SIZE, offset)

    if total == 0:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")]
        ])
        text = "<b>Ожидают решения</b>\nПока пусто."
        if isinstance(evt, Message):
            await evt.answer(text, reply_markup=kb)
        else:
            await evt.message.edit_text(text, reply_markup=kb)
        return

    buttons = []
    for r in rows:
        owner = fmt_owner(r["tg_user"], r["username"], r["first_name"], r["last_name"])
        title = f"{owner} — {r['device_name']}  (#{r['id']})"
        buttons.append([InlineKeyboardButton(text=title, callback_data=f"req:open:{r['id']}")])
        # ВАЖНО: прокидываем текущую страницу в колбэки
        buttons.append([
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"req:approve:{r['id']}:{page}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"req:reject:{r['id']}:{page}"),
        ])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"list:pending:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"list:pending:{page+1}"))
    if nav:
        buttons.append(nav)

    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    text = f"<b>Ожидают решения</b>\nВсего: {total}"
    if isinstance(evt, Message):
        await evt.answer(text, reply_markup=kb)
    else:
        await evt.message.edit_text(text, reply_markup=kb)
