import asyncio
import logging

from aiogram import Bot, Dispatcher, Router, F
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    BufferedInputFile,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from settings import settings
from handlers import user as user_handlers
from handlers import admin as admin_handlers
from state import RequestPeer
from services.wireguard import add_peer, fetch_client_conf_and_qr
from db import insert_peer, upsert_user


# ──────────────────────────── Логирование ────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

# ──────────────────────── Инициализация бота ─────────────────────────────
bot = Bot(
    settings.BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher(storage=MemoryStorage())

# ──────────────────────────── Роутеры ────────────────────────────────────
rt = Router()
dp.include_router(user_handlers.rt)
dp.include_router(admin_handlers.rt)
dp.include_router(rt)  # временно: старые хендлеры тут


# ───────────────────────────── Хендлеры ──────────────────────────────────
@rt.message(Command("new"))
async def cmd_new(m: Message, state: RequestPeer):
    await upsert_user(
        m.from_user.id,
        m.from_user.username,
        m.from_user.first_name,
        m.from_user.last_name,
    )
    await m.answer("Как назвать устройство? <code>MacBook-Air</code> и т.д.")
    await state.set_state(RequestPeer.waiting_name)


@rt.message(RequestPeer.waiting_name, F.text)
async def got_name(m: Message, state: RequestPeer):
    name = m.text.strip()
    await state.clear()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Одобрить",
                    callback_data=f"ok|{m.from_user.id}|{name}|{m.from_user.full_name}",
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить",
                    callback_data=f"no|{m.from_user.id}|{name}|{m.from_user.full_name}",
                ),
            ]
        ]
    )

    for admin in settings.ADMIN_IDS:
        await bot.send_message(
            admin,
            (
                f"<b>Запрос VPN</b>\n"
                f"👤 <a href=\"tg://user?id={m.from_user.id}\">{m.from_user.full_name}</a>\n"
                f"💻 <code>{name}</code>"
            ),
            reply_markup=kb,
        )

    await m.answer("Запрос отправлен администратору, ждите решения.")


@rt.callback_query(F.data.startswith(("ok|", "no|")))
async def admin_decision(cb: CallbackQuery):
    try:
        action, uid, device_name, user_name = cb.data.split("|")
        user_id = int(uid)

        await cb.answer("Одобрено" if action == "ok" else "Отклонено")

        new_text = (
            f"<b>Запрос VPN</b>\n"
            f"👤 <a href=\"tg://user?id={user_id}\">{user_name}</a>\n"
            f"💻 <code>{device_name}</code>\n\n"
            f"{'✅ Одобрено' if action == 'ok' else '❌ Отклонено'}"
        )
        await cb.message.edit_text(new_text)

        if action == "no":
            await bot.send_message(user_id, "Администратор отклонил запрос.")
            return

        # ── Создание пира через WireGuard и запись в БД
        pubkey, ip_oct, cidr, conf_path = await add_peer(device_name)
        await insert_peer(user_id, device_name, pubkey, ip_oct, cidr, conf_path)

        conf_text, qr_png = await fetch_client_conf_and_qr(conf_path)

        await bot.send_photo(
            user_id,
            BufferedInputFile(qr_png, filename="qr.png"),
            caption=f"Туннель <b>{device_name}</b>. Сканируйте QR в WireGuard.",
        )
        await bot.send_document(
            user_id,
            BufferedInputFile(conf_text.encode(), filename=f"{device_name}.conf"),
        )

        await cb.answer("Профиль создан!")
    except Exception as e:
        logging.exception(f"Ошибка при обработке решения администратора: {e}")
        await cb.answer("Произошла ошибка.", show_alert=True)


# ───────────────────────────── main() ────────────────────────────────────
async def main():
    try:
        logging.info("🔄 Удаляем вебхук и сбрасываем апдейты…")
        await bot.delete_webhook(drop_pending_updates=True)

        me = await bot.get_me()
        logging.info(f"✅ Бот @{me.username} ({me.id}) запущен и готов к работе")

        logging.info("🚀 Запуск polling…")
        await dp.start_polling(bot)

    except Exception:
        logging.exception("❌ Фатальная ошибка при запуске бота")
        raise


if __name__ == "__main__":
    asyncio.run(main())
