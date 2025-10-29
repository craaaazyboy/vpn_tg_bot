# presenters/admin_ui.py
from typing import Optional, List, Iterable, Dict, Any
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils.format import fmt_bytes, fmt_owner

PAGE_SIZE = 10  # для навигации в подписях (если надо)

def kb_peer_card_safe(tg_user: Optional[int], peer_id: int, username: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if tg_user and username:
        rows.append([InlineKeyboardButton(text="💬 Написать", url=f"tg://user?id={tg_user}")])
    rows.append([
        InlineKeyboardButton(text="🔁 Отправить конфиг", callback_data=f"peer:resend:{peer_id}"),
        InlineKeyboardButton(text="⛔ Отключить",       callback_data=f"peer:revoke:{peer_id}"),
    ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_list_peers(status: str, page: int, total_pages: int, rows: Iterable[Dict[str, Any]]) -> InlineKeyboardMarkup:
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

def kb_pending_row(req_id: int, page: int) -> List[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(text="✅ Одобрить", callback_data=f"req:approve:{req_id}:{page}"),
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"req:reject:{req_id}:{page}")
    ]

def kb_back_to_pending(page: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"list:pending:{page}")]
    ])

def kb_pending_list(page: int, total_pages: int) -> List[InlineKeyboardButton]:
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"list:pending:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"{page}/{total_pages}", callback_data="noop"))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"list:pending:{page+1}"))
    return nav

def render_peer_card_text(row: Dict[str, Any]) -> str:
    owner = fmt_owner(row["tg_user"], row.get("username"), row.get("first_name"), row.get("last_name"))
    last = row.get("last_handshake")
    last_hs = last.strftime("%Y-%m-%d %H:%M:%S") if last else "—"
    return (
        f"<b>{row['device_name']}</b>\n"
        f"👤 {owner}\n"
        f"🔑 <code>{row['peer_pubkey']}</code>\n"
        f"🌐 {row['allowed_cidr']}\n"
        f"⏱ Last HS: {last_hs}\n"
        f"📥 RX: {fmt_bytes(row.get('rx_bytes',0))}    📤 TX: {fmt_bytes(row.get('tx_bytes',0))}"
    )

def render_online_list(items: Iterable[Dict[str, Any]], meta: Dict[str, Dict[str, Any]]) -> str:
    lines = []
    for s in sorted(items, key=lambda x: x["last_handshake"], reverse=True):
        m = meta.get(s["pubkey"], {})
        owner = fmt_owner(m.get("tg_id"), m.get("username"), m.get("first_name"), m.get("last_name"))
        dev   = (m.get("device_name") or "?").strip()
        rx_h, tx_h = fmt_bytes(s["rx_bytes"]), fmt_bytes(s["tx_bytes"])
        lines.append(f"• {owner} — <code>{dev}</code>  📥 {rx_h} / 📤 {tx_h}")
    return "🟢 <b>Сейчас онлайн</b>\n" + ("\n".join(lines) if lines else "Никто не подключён.")

def render_request_card(r: Dict[str, Any]) -> str:
    owner = fmt_owner(r["tg_user"], r["username"], r["first_name"], r["last_name"])
    return (
        f"<b>Заявка #{r['id']}</b>\n"
        f"👤 {owner} (id: <code>{r['tg_user']}</code>)\n"
        f"💻 <code>{r['device_name']}</code>\n"
        f"Статус: pending"
    )
