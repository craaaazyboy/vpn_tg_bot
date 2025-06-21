from __future__ import annotations
import asyncio, io, logging, os
from typing import Final

import aiohttp
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)

import db
from state import RequestPeer

# ────────── env ────────────────────────────────────────────────────────────
BOT_TOKEN: Final[str] = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS: Final[list[int]] = [int(i) for i in
                               os.getenv("ADMIN_ID", "").split(",") if i]
WG_URL:  Final[str] = os.getenv("WG_API_URL", "http://wg-api:3000").rstrip("/")
WG_JWT:  Final[str] = os.getenv("WG_JWT", "").strip()
auth_headers = {"Authorization": f"Bearer {WG_JWT}"}

# ────────── aiogram init ───────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp  = Dispatcher()
rt  = Router()
dp.include_router(rt)

# ────────── REST helpers ───────────────────────────────────────────────────
async def api_create_peer(name: str) -> dict:
    async with aiohttp.ClientSession(headers=auth_headers) as s:
        async with s.post(f"{WG_URL}/api/clients", json={"name": name}) as r:
            r.raise_for_status()
            return await r.json()

async def api_get_file(peer_id: int, fmt: str) -> bytes | str:
    async with aiohttp.ClientSession(headers=auth_headers) as s:
        async with s.get(f"{WG_URL}/api/clients/{peer_id}?format={fmt}") as r:
            r.raise_for_status()
            return await (r.read() if fmt == "qr" else r.text())

# ────────── user flow ──────────────────────────────────────────────────────
@rt.message(Command("start"))
async def cmd_start(m: Message):
    await m.answer("Привет! Отправь /new, чтобы запросить VPN-профиль.")

@rt.message(Command("new"))
async def cmd_new(m: Message, state: RequestPeer):
    await m.answer("Как назвать устройство? <code>MacBook-Air</code> и т.д.")
    await state.set_state(RequestPeer.waiting_name)

@rt.message(RequestPeer.waiting_name, F.text.len() > 1)
async def got_name(m: Message, state: RequestPeer):
    name = m.text.strip()
    await state.clear()

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Одобрить",
                             callback_data=f"ok|{m.from_user.id}|{name}"),
        InlineKeyboardButton(text="❌ Отклонить",
                             callback_data=f"no|{m.from_user.id}")
    ]])

    for admin in ADMIN_IDS:
        await bot.send_message(
            admin,
            f"<b>Запрос VPN</b>\n👤 <a href=\"tg://user?id={m.from_user.id}\">"
            f"{m.from_user.full_name}</a>\n💻 <code>{name}</code>",
            reply_markup=kb)

    await m.answer("Запрос отправлен администратору, ждите решения.")

# ────────── admin buttons ──────────────────────────────────────────────────
@rt.callback_query(F.data.regexp(r"^(ok|no)\|"))
async def admin_decision(cb: CallbackQuery):
    action, uid, *rest = cb.data.split("|")
    user_id = int(uid)

    if action == "no":
        await bot.send_message(user_id, "Администратор отклонил запрос.")
        await cb.answer("Отклонено")
        return

    device_name = rest[0]

    # создаём peer
    peer = await api_create_peer(device_name)
    peer_id = int(peer["id"])

    # качаем файлы
    qr    = await api_get_file(peer_id, "qr")
    conf  = await api_get_file(peer_id, "conf")

    # сохраняем в БД
    await db.save_peer(user_id, device_name, peer_id)

    # отправка пользователю
    await bot.send_photo(
        user_id,
        BufferedInputFile(qr, filename="qr.png"),
        caption=f"Туннель <b>{device_name}</b>.\nСканируйте QR в WireGuard.")
    await bot.send_document(
        user_id,
        BufferedInputFile(conf.encode(), filename=f"{device_name}.conf"))

    await cb.answer("Профиль создан!")

# ────────── run ────────────────────────────────────────────────────────────
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
