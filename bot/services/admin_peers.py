# services/admin_peers.py
from typing import Tuple
from aiogram.types import InlineKeyboardMarkup
from presenters.admin_ui import kb_list_peers, kb_peer_card_safe, render_peer_card_text, render_online_list
from db import list_peers_page, count_peers, get_peer_by_id, owners_by_pubkeys, revoke_peer
from services.wireguard import wg_dump_stats, ssh_revoke_by_device_or_pubkey, fetch_client_conf_and_qr
from aiogram.types import BufferedInputFile

PAGE_SIZE = 10

async def fetch_peers_page(status: str, page: int):
    offset = (page - 1) * PAGE_SIZE
    total = await count_peers(status)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE if total else 1
    rows = await list_peers_page(status, PAGE_SIZE, offset)
    return total, total_pages, rows

def build_peers_keyboard(status: str, page: int, total_pages: int, rows) -> InlineKeyboardMarkup:
    return kb_list_peers(status, page, total_pages, rows)

async def open_peer_text_and_kb(peer_id: int) -> Tuple[str, InlineKeyboardMarkup | None]:
    row = await get_peer_by_id(peer_id)
    if not row:
        return "Пир не найден", None

    # подмешаем свежую статистику
    try:
        stats = await wg_dump_stats()
        s = next((x for x in stats if x["pubkey"] == row["peer_pubkey"]), None)
        if s:
            row["rx_bytes"] = s["rx_bytes"]
            row["tx_bytes"] = s["tx_bytes"]
            row["last_handshake"] = s["last_handshake"]
    except Exception:
        pass

    return render_peer_card_text(row), kb_peer_card_safe(row["tg_user"], row["id"])

async def do_revoke(peer_id: int, actor_id: int) -> str:
    row = await get_peer_by_id(peer_id)
    if not row:
        return "Уже отключён или не найден."
    await ssh_revoke_by_device_or_pubkey(device_name=row["device_name"], pubkey=row["peer_pubkey"])
    await revoke_peer(peer_id, actor_id)
    return "Пир отключён ✅"

async def resend_conf(peer_id: int, bot) -> str:
    row = await get_peer_by_id(peer_id)
    if not row or not row["tg_user"] or not row["conf_path"]:
        return "Конфиг не найден или у пира нет владельца."
    conf_text, qr_png = await fetch_client_conf_and_qr(row["conf_path"])
    await bot.send_photo(
        row["tg_user"],
        photo=BufferedInputFile(qr_png, filename=f"{row['device_name']}.png"),
        caption=f"Туннель <b>{row['device_name']}</b>. Сканируйте QR в WireGuard."
    )
    await bot.send_document(
        row["tg_user"],
        document=BufferedInputFile(conf_text.encode(), filename=f"{row['device_name']}.conf")
    )
    return "Конфиг отправлен ✅"

async def render_online_text() -> str:
    stats = await wg_dump_stats()
    online = [s for s in stats if s.get("last_handshake")]
    meta = await owners_by_pubkeys([s["pubkey"] for s in online])
    return render_online_list(online, meta)
