from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from state import RequestPeer
from keyboards import kb_user_main
from db import upsert_user, get_pool, is_admin, create_access_request
from settings import settings

rt = Router()

# /start — показываем меню; админам добавляем кнопку "Админка"
@rt.message(CommandStart())
async def start(m: Message):
    await upsert_user(m.from_user.id, m.from_user.username, m.from_user.first_name, m.from_user.last_name)
    admin_flag = await is_admin(m.from_user.id)
    await m.answer("Привет 👋\nВыберите действие кнопками ниже:", reply_markup=kb_user_main(is_admin=admin_flag))

# Помощь
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

# Кнопка: Запросить VPN → спрашиваем имя устройства (FSM)
@rt.message(F.text.casefold() == "➕ запросить vpn".casefold())
async def ask_device_name(m: Message, state: FSMContext):
    await m.answer("Как назвать устройство? Например: <code>MacBook-Air</code>")
    await state.set_state(RequestPeer.waiting_name)

# Пользователь ввёл имя устройства → создаём заявку и уведомляем админов (БЕЗ ✅/❌ в личке)
@rt.message(RequestPeer.waiting_name, F.text.len() > 1)
async def got_name(m: Message, state: FSMContext):
    device_name = m.text.strip()
    await state.clear()

    # создаём заявку в pending
    req_id = await create_access_request(m.from_user.id, device_name)

    # сообщаем пользователю
    admin_flag = await is_admin(m.from_user.id)
    await m.answer(
        "✅ Заявка создана.\n"
        "Ожидайте решения администратора. Вы получите уведомление, как только её рассмотрят.",
        reply_markup=kb_user_main(is_admin=admin_flag),
    )

    # пушим уведомление админам (без approve-кнопок!)
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
            reply_markup=kb_admin_hint
        )

# Кнопка: Мои устройства
@rt.message(F.text.casefold() == "📦 мои устройства".casefold())
async def my_devices(m: Message):
    pool = await get_pool()
    async with pool.acquire() as con:
        rows = await con.fetch(
            """
            SELECT id, device_name, allowed_cidr, status, last_handshake
            FROM peers
            WHERE tg_user=$1
            ORDER BY id
            """,
            m.from_user.id,
        )

    admin_flag = await is_admin(m.from_user.id)

    if not rows:
        return await m.answer("У тебя пока нет устройств.", reply_markup=kb_user_main(is_admin=admin_flag))

    devices_text = "<b>📦 Твои устройства:</b>\n\n" + "\n".join(
        f"#{r['id']} <code>{r['device_name']}</code> — {r['status']} ({r['allowed_cidr']})"
        for r in rows
    )
    await m.answer(devices_text, reply_markup=kb_user_main(is_admin=admin_flag))

# Также поддерживаем /new (аналог кнопки)
@rt.message(Command("new"))
async def cmd_new(m: Message, state: FSMContext):
    await upsert_user(m.from_user.id, m.from_user.username, m.from_user.first_name, m.from_user.last_name)
    await m.answer("Как назвать устройство? <code>MacBook-Air</code> и т.д.")
    await state.set_state(RequestPeer.waiting_name)
