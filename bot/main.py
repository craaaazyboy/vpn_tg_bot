import html
import asyncio
import logging
import os
from typing import Final
import asyncssh
from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    BufferedInputFile,
)

from state import RequestPeer

# ────────── env ────────────────────────────────────────────────────────────
BOT_TOKEN: Final[str] = os.getenv("BOT_TOKEN", "").strip()
ADMIN_IDS: Final[list[int]] = [int(i) for i in os.getenv("ADMIN_ID", "").split(",") if i]
WG_SSH_HOST: Final[str] = os.getenv("WG_SSH_HOST")
WG_SSH_USER: Final[str] = os.getenv("WG_SSH_USER", "root")
WG_SSH_KEY: Final[str] = os.getenv("WG_SSH_KEY", "/run/secrets/vpn_ssh_key")

# ────────── aiogram init ───────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")

bot = Bot(BOT_TOKEN, parse_mode=ParseMode.HTML)
dp = Dispatcher()
rt = Router()
dp.include_router(rt)

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
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ok|{m.from_user.id}|{name}|{m.from_user.full_name}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"no|{m.from_user.id}|{name}|{m.from_user.full_name}")
    ]])

    for admin in ADMIN_IDS:
        await bot.send_message(
            admin,
            f"<b>Запрос VPN</b>\n👤 <a href=\"tg://user?id={m.from_user.id}\">"
            f"{m.from_user.full_name}</a>\n💻 <code>{name}</code>",
            reply_markup=kb)

    await m.answer("Запрос отправлен администратору, ждите решения.")

async def get_next_free_ip(conn) -> int:
    """
    Парсим конфиг-файл напрямую, чтобы гарантированно получить последний IP.
    """
    result = await conn.run(
        "grep 'AllowedIPs' /etc/wireguard/wg0.conf | "
        "grep -oP '10\\.66\\.66\\.\\K\\d+' | "
        "sort -n | tail -1", check=False
    )
    last_ip = result.stdout.strip()
    if last_ip.isdigit():
        return int(last_ip) + 1
    return 2  # начальный IP, если конфиг пуст


async def ssh_addconf(device_name: str) -> None:
    logging.info("📡 SSH: добавляем пир '%s'", device_name)
    async with asyncssh.connect(
        WG_SSH_HOST,
        username=WG_SSH_USER,
        client_keys=[WG_SSH_KEY],
        known_hosts=None,
    ) as conn:
        next_ip = await get_next_free_ip(conn)
        cmd = f"printf '1\n{device_name}\n{next_ip}\n{next_ip}\n' | bash ~/wireguard-install.sh"
        result = await conn.run(cmd, check=True)
        logging.info("✅ wireguard-install output:\n%s", result.stdout)

# ────────── admin buttons ──────────────────────────────────────────────────
@rt.callback_query(F.data.regexp(r"^(ok|no)\|"))
async def admin_decision(cb: CallbackQuery):
    action, uid, *rest = cb.data.split("|")
    user_id = int(uid)


    # 1) берём raw_name только если это OK, иначе default
    device_name = rest[0]
    user_name = rest[1]
    # 3) отвечаем на callback — убираем крутилку
    await cb.answer(text="Одобрено" if action == "ok" else "Отклонено")

    # 4) редактируем сообщение в админском чате (удаляем клавиатуру)
    decision_text = "✅ Одобрено" if action == "ok" else "❌ Отклонено"
    new_text = (
        f"<b>Запрос VPN</b>\n"
        f"👤 <a href=\"tg://user?id={user_id}\">{user_name}</a>\n"
        f"💻 <code>{device_name}</code>\n\n"
        f"{decision_text}"
    )

    await cb.message.edit_text(new_text, parse_mode="HTML")

    if action == "no":
        await bot.send_message(user_id, "Администратор отклонил запрос.")
        await cb.answer("Отклонено")
        return

    await ssh_addconf(device_name)

    async with asyncssh.connect(
        WG_SSH_HOST,
        username=WG_SSH_USER,
        client_keys=[WG_SSH_KEY],
        known_hosts=None,
    ) as conn:
        conf_path = f"/root/wg0-client-{device_name}.conf"

        result = await conn.run(f"cat {conf_path}", check=True)
        conf_data = result.stdout

        # Здесь добавляем encoding=None для бинарного файла
        qr_result = await conn.run(f"qrencode -t png -o - < {conf_path}", encoding=None, check=True)
        qr_bytes = qr_result.stdout  # теперь правильно получаем bytes напрямую

    await bot.send_photo(
        user_id,
        BufferedInputFile(qr_bytes, filename="qr.png"),
        caption=f"Туннель <b>{device_name}</b>.\nСканируйте QR в WireGuard."
    )
    await bot.send_document(
        user_id,
        BufferedInputFile(conf_data.encode(), filename=f"{device_name}.conf")
    )

    await cb.answer("Профиль создан!")

# ────────── run ────────────────────────────────────────────────────────────
# ────────── run ────────────────────────────────────────────────────────────
async def main():
    # 1) снимаем веб-хук
    ok = await bot.delete_webhook(drop_pending_updates=True)
    logging.info("delete_webhook → %s", ok)

    info = await bot.get_webhook_info()
    logging.info("WebhookInfo после delete: %s", info)

    # 2) запускаем polling
    await dp.start_polling(bot, skip_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
