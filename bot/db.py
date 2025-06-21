# bot/db.py
from __future__ import annotations

import os, asyncpg, asyncio
from models import DDL          # ← берём схему отсюда

_DSN = os.getenv("DATABASE_URL")  # построй DSN в .env

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(dsn=_DSN)
        # бесплатно прогоняем DDL при первом подключении
        async with _pool.acquire() as con:
            await con.execute(DDL)
    return _pool


async def save_peer(tg_user: int, device_name: str, peer_id: int) -> None:
    pool = await get_pool()
    async with pool.acquire() as con:
        await con.execute(
            "INSERT INTO peers (tg_user, device_name, peer_id)"
            " VALUES ($1, $2, $3)",
            tg_user,
            device_name,
            peer_id,
        )


# локальный тест: python bot/db.py
if __name__ == "__main__":
    asyncio.run(get_pool())
    print("DB ready ✔")
