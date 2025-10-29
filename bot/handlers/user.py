from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile
)
from aiogram.fsm.context import FSMContext

from state import RequestPeer
from keyboards import kb_user_main
from db import (
    upsert_user, is_admin, create_access_request,
    list_user_peers, get_peer_owned_by
)
from services.wireguard import fetch_client_conf_and_qr, wg_dump_stats
from settings import settings
from utils.format import fmt_bytes

rt = Router()


# ──────────────── /start ────────────────
@rt.message(CommandStart())
async def start(m: Message):
    await upsert_user(
        m.from_user.id,
        m.from_user.username,
        m.from_user.first_name,
        m.from_user.last_name,
    )
    admin_flag = await is_admin(m.from_user.id)
    await m.answer(
        "Привет 👋\nВыберите действие кнопками ниже:",
        reply_markup=kb_user_main(is_admin=admin_flag),
    )


# ──────────────── Помощь ────────────────
@rt.message(F.text.casefold() == "ℹ️ помощь".casefold())
async def help_(m: Message):
    admin_flag = await is_admin(m.from_user.id)
    await m.answer(
        "📘 <b>Справка</b>\n\n"
        "• «➕ Запросить VPN» — создать новый профиль\n"
        "• «📦 Мои устройства» — список твоих устройств и статус\n"
        "• «🛠 Админка» — доступно только администраторам",
        reply_markup=kb_user_main(is_admin=admin_flag),
    )


# ──────────────── Запрос VPN ────────────────
@rt.message(F.text.casefold() == "➕ запросить vpn".casefold())
async def ask_device_name(m: Message, state: FSMContext):
    await m.answer("Как назвать устройство? Например: <code>MacBook-Air</code>")
    await state.set_state(RequestPeer.waiting_name)


@rt.message(RequestPeer.waiting_name, F.text.len() > 1)
async def got_name(m: Message, state: FSMContext):
    device_name = m.text.strip()
    await state.clear()

    req_id = await create_access_request(m.from_user.id, device_name)

    admin_flag = await is_admin(m.from_user.id)
    await m.answer(
        "✅ Заявка создана.\n"
        "Ожидайте решения администратора. Вы получите уведомление, как только её рассмотрят.",
        reply_markup=kb_user_main(is_admin=admin_flag),
    )

    # уведомление администраторам (без approve-кнопок)
    kb_admin_hint = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Перейти к ожидающим", callback_data="list:pending:1")
    ]])
    for admin in settings.ADMIN_IDS:
        await m.bot.send_message(
            admin,
            f"<b>Новая заявка</b>\n"
            f"👤 <a href=\"tg://user?id={m.from_user.id}\">{m.from_user.full_name}</a>\n"
            f"💻 <code>{device_name}</code>\n"
            f"🆔 Заявка #{req_id}",
            reply_markup=kb_admin_hint,
        )


# ──────────────── Мои устройства ────────────────
def _kb_my_devices(rows):
    kb = []
    for r in rows:
        title = f"{r['device_name']} • {r['allowed_cidr'] or '—'}"
        kb.append([InlineKeyboardButton(text=title, callback_data=f"my:open:{r['id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="my:back")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


def _kb_my_peer(peer_id: int, has_conf: bool):
    rows = []
    if has_conf:
        rows.append([InlineKeyboardButton(text="🔁 Прислать конфиг", callback_data=f"my:resend:{peer_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ К устройствам", callback_data="my:list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@rt.message(F.text.casefold() == "📦 мои устройства".casefold())
async def my_devices(m: Message):
    rows = await list_user_peers(m.from_user.id)
    admin_flag = await is_admin(m.from_user.id)

    if not rows:
        return await m.answer("У тебя пока нет устройств.", reply_markup=kb_user_main(is_admin=admin_flag))

    text = f"<b>📦 Твои устройства</b>\nВсего: {len(rows)}\nНажми, чтобы открыть карточку."
    kb = _kb_my_devices(rows)
    await m.answer(text, reply_markup=kb)


@rt.callback_query(F.data == "my:list")
async def my_list_cb(cb: CallbackQuery):
    rows = await list_user_peers(cb.from_user.id)
    admin_flag = await is_admin(cb.from_user.id)

    if not rows:
        await cb.message.edit_text("У тебя пока нет устройств.", reply_markup=kb_user_main(is_admin=admin_flag))
        return await cb.answer()

    text = f"<b>📦 Твои устройства</b>\nВсего: {len(rows)}\nНажми, чтобы открыть карточку."
    kb = _kb_my_devices(rows)
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


@rt.callback_query(F.data == "my:back")
async def my_back(cb: CallbackQuery):
    admin_flag = await is_admin(cb.from_user.id)
    await cb.message.edit_text("Меню", reply_markup=kb_user_main(is_admin=admin_flag))
    await cb.answer()


# ──────────────── Карточка устройства ────────────────
@rt.callback_query(F.data.startswith("my:open:"))
async def my_open_peer(cb: CallbackQuery):
    peer_id = int(cb.data.split(":")[-1])
    row = await get_peer_owned_by(peer_id, cb.from_user.id)
    if not row:
        return await cb.answer("Устройство не найдено.", show_alert=True)

    # обновляем актуальную статистику
    rx = row["rx_bytes"]
    tx = row["tx_bytes"]
    last = row["last_handshake"]
    try:
        stats = await wg_dump_stats()
        s = next((x for x in stats if x["pubkey"] == row["peer_pubkey"]), None)
        if s:
            rx = s["rx_bytes"]
            tx = s["tx_bytes"]
            last = s["last_handshake"]
    except Exception:
        pass

    last_hs = last.strftime("%Y-%m-%d %H:%M:%S") if last else "—"
    pubkey_short = (row["peer_pubkey"][:16] + "…") if row.get("peer_pubkey") else "—"

    text = (
        f"<b>{row['device_name']}</b>\n"
        f"🔑 <code>{pubkey_short}</code>\n"
        f"🌐 {row.get('allowed_cidr') or '—'}\n"
        f"⏱ Last HS: {last_hs}\n"
        f"📥 RX: {fmt_bytes(rx)}    📤 TX: {fmt_bytes(tx)}"
    )
    kb = _kb_my_peer(peer_id, bool(row.get("conf_path")))
    await cb.message.edit_text(text, reply_markup=kb)
    await cb.answer()


# ──────────────── Повторная отправка конфига ────────────────
@rt.callback_query(F.data.startswith("my:resend:"))
async def my_resend(cb: CallbackQuery):
    peer_id = int(cb.data.split(":")[-1])
    row = await get_peer_owned_by(peer_id, cb.from_user.id)

    if not row or not row.get("conf_path"):
        return await cb.answer("Конфиг не найден.", show_alert=True)

    conf_text, qr_png = await fetch_client_conf_and_qr(row["conf_path"])
    await cb.message.bot.send_photo(
        cb.from_user.id,
        photo=BufferedInputFile(qr_png, filename=f"{row['device_name']}.png"),
        caption=f"Туннель <b>{row['device_name']}</b>. Сканируйте QR в WireGuard."
    )
    await cb.message.bot.send_document(
        cb.from_user.id,
        document=BufferedInputFile(conf_text.encode(), filename=f"{row['device_name']}.conf")
    )
    await cb.answer("Конфиг отправлен ✅")


# ──────────────── Альтернатива /new ────────────────
@rt.message(Command("new"))
async def cmd_new(m: Message, state: FSMContext):
    await upsert_user(m.from_user.id, m.from_user.username, m.from_user.first_name, m.from_user.last_name)
    await m.answer("Как назвать устройство? <code>MacBook-Air</code> и т.д.")
    await state.set_state(RequestPeer.waiting_name)
