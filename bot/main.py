import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from settings import settings
from db import init_models
from handlers.user import rt as user_rt
from handlers.admin import rt as admin_rt
from handlers.ikev2_user import rt as ikev2_user_rt
from handlers.ikev2_admin import rt_ikev2_admin


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Ensure DB schema exists (creates missing tables). Safe to run on each start.
    await init_models()

    bot = Bot(token=settings.BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())

    # Routers
    dp.include_router(user_rt)
    dp.include_router(ikev2_user_rt)
    dp.include_router(admin_rt)
    dp.include_router(rt_ikev2_admin)

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
