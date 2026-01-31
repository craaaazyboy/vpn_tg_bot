from __future__ import annotations
import logging
logger = logging.getLogger(__name__)
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from db import create_download
from settings import settings
from db import (
    is_admin,
    get_ikev2_request,
    decide_ikev2_request,
    create_ikev2_account,
    list_ikev2_requests,
    list_ikev2_accounts,
    get_ikev2_account,
    revoke_ikev2_account,
)
from services.ikev2 import (
    generate_username,
    generate_password,
    ensure_user_on_server,
    revoke_user_on_server,
    fetch_ca_cert_der_b64,
    build_ios_mobileconfig,
    build_android_sswan,
)

# Primary router export (main.py expects this name)
rt = Router()
# Backward-compatible alias if you used this name elsewhere
rt_ikev2_admin = rt

PAGE_SIZE = 8


def _kb_admin_back() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")]])


def _kb_req_actions(req_id: int, page: int | None = None) -> InlineKeyboardMarkup:
    suffix = f":{page}" if page is not None else ""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ikev2:req:approve:{req_id}{suffix}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"ikev2:req:reject:{req_id}{suffix}"),
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"ikev2:list:pending:{page or 1}")],
        ]
    )


@rt.callback_query(F.data.startswith("ikev2:list:pending:"))
async def ikev2_pending_list(cb: CallbackQuery):
    if not await is_admin(cb.from_user.id):
        return
    _, _, _, page_s = cb.data.split(":", 3)
    page = max(1, int(page_s))
    offset = (page - 1) * PAGE_SIZE
    rows = await list_ikev2_requests(status="pending", limit=PAGE_SIZE, offset=offset)

    if not rows:
        await cb.message.edit_text("🔐 IKEv2: ожидающих заявок нет.", reply_markup=_kb_admin_back())
        await cb.answer()
        return

    lines = ["<b>🔐 IKEv2: заявки (ожидают)</b>"]
    kb_rows = []
    for r in rows:
        lines.append(f"• #{r['id']} • {r['device_name']} • tg:{r['tg_user']}")
        kb_rows.append([
            InlineKeyboardButton(text=f"Открыть #{r['id']}", callback_data=f"ikev2:req:open:{r['id']}:{page}")
        ])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"ikev2:list:pending:{page-1}"))
    if len(rows) == PAGE_SIZE:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"ikev2:list:pending:{page+1}"))
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton(text="⬅️ Админ-меню", callback_data="admin:back")])

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )
    await cb.answer()


@rt.callback_query(F.data.startswith("ikev2:req:open:"))
async def ikev2_req_open(cb: CallbackQuery):
    if not await is_admin(cb.from_user.id):
        return

    parts = cb.data.split(":")
    req_id = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else 1

    req = await get_ikev2_request(req_id)
    if not req:
        await cb.answer("Заявка не найдена", show_alert=True)
        return
    if req["status"] != "pending":
        await cb.answer("Заявка уже обработана", show_alert=True)
        return

    txt = (
        "<b>🔐 IKEv2: заявка</b>\n"
        f"ID: #{req['id']}\n"
        f"Пользователь: tg:{req['tg_user']}\n"
        f"Устройство: {req['device_name']}\n"
        f"Платформа: {req['platform']}\n"
        f"Создано: {req['created_at']}"
    )
    await cb.message.edit_text(txt, reply_markup=_kb_req_actions(req_id, page), parse_mode="HTML")
    await cb.answer()


def _ios_text() -> str:
    return (
        "<b>IKEv2 для iOS</b>\n\n"
        "1) Нажми <b>Скачать профиль</b> → откроется Safari\n"
        "2) Установи профиль\n"
        "3) Если попросит — введи код-пароль iPhone\n"
        "4) Включи доверие сертификату (если потребуется):\n"
        "<b>Настройки → Основные → Об этом устройстве → Доверие сертификатам</b>\n"
        "5) Подключение: <b>Настройки → VPN</b>\n"
    )

def _android_text() -> str:
    return (
        "<b>IKEv2 для Android (strongSwan)</b>\n\n"
        "1) Установи <b>strongSwan VPN Client</b>\n"
        "2) Нажми <b>Скачать профиль</b> и импортируй в strongSwan\n"
        "3) Подключайся из приложения\n"
    )


async def _send_profile(bot, user_tg_id: int, platform: str, device_name: str, username: str, password: str):
    ca_b64 = await fetch_ca_cert_der_b64()

    if platform == "ios":
        content = build_ios_mobileconfig(
            profile_name=f"{settings.BRAND_NAME} IKEv2 ({device_name})",
            server_addr=settings.IKEV2_SERVER_ADDR,
            remote_id=settings.IKEV2_REMOTE_ID,
            username=username,
            password=password,
            ca_cert_der_b64=ca_b64,
        )
        filename = f"{settings.BRAND_NAME}_IKEv2_{device_name}.mobileconfig".replace(" ", "_")
        mime = "application/x-apple-aspen-config"
        instructions = _ios_text()
    else:
        content = build_android_sswan(
            profile_name=f"{settings.BRAND_NAME} IKEv2 ({device_name})",
            server_addr=settings.IKEV2_SERVER_ADDR,
            remote_id=settings.IKEV2_REMOTE_ID,
            username=username,
            password=password,
            ca_cert_der_b64=ca_b64,
        )
        filename = f"{settings.BRAND_NAME}_IKEv2_{device_name}.sswan".replace(" ", "_")
        mime = "application/octet-stream"
        instructions = _android_text()

    token = await create_download(
        content=content,
        filename=filename,
        mime=mime,
        ttl_seconds=getattr(settings, "DOWNLOAD_TTL_SECONDS", 900),
    )

    base = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
    url = f"{base}/d/{token}"

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬇️ Скачать профиль", url=url)]
    ])

    await bot.send_message(user_tg_id, "Профиль готов ✅", reply_markup=kb)
    await bot.send_message(user_tg_id, instructions, parse_mode="HTML")
    await bot.send_message(user_tg_id, f"Логин:\n<code>{username}</code>", parse_mode="HTML")
    await bot.send_message(user_tg_id, f"Пароль:\n<code>{password}</code>", parse_mode="HTML")


@rt.callback_query(F.data.startswith("ikev2:req:approve:"))
async def ikev2_req_approve(cb: CallbackQuery):
    if not await is_admin(cb.from_user.id):
        return

    parts = cb.data.split(":")
    req_id = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else None

    req = await get_ikev2_request(req_id)
    if not req or req["status"] != "pending":
        await cb.answer("Заявка не найдена/уже обработана", show_alert=True)
        return

    # обязательно ответить сразу (иначе 'query is too old')
    await cb.answer("⏳ Создаю профиль…", cache_time=1)
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    try:
        username = generate_username(req["tg_user"], req["device_name"], suffix_len=4)
        password = generate_password()

        await ensure_user_on_server(username, password)
        await create_ikev2_account(
            tg_user=req["tg_user"],
            platform=req["platform"],
            device_name=req["device_name"],
            username=username,
        )

        # ВАЖНО: отправку делаем ДО decide_ikev2_request
        # чтобы при падении отправки заявка осталась pending, а не "approved без профиля"
        await _send_profile(cb.bot, req["tg_user"], req["platform"], req["device_name"], username, password)

        await decide_ikev2_request(req_id, decided_by=cb.from_user.id, status="approved")
        await cb.message.edit_text("✅ Готово. Профиль отправлен пользователю.", reply_markup=_kb_admin_back())

    except Exception as e:
        logger.exception("ikev2 approve failed req_id=%s tg_user=%s device=%s", req_id, req.get("tg_user"), req.get("device_name"))
        # покажем админам краткую причину (без огромных трейсбеков)
        err = f"{type(e).__name__}: {e}"
        try:
            await cb.message.edit_text(f"❌ Ошибка создания/отправки профиля:\n<code>{err}</code>", reply_markup=_kb_admin_back(), parse_mode="HTML")
        except Exception:
            pass

@rt.callback_query(F.data.startswith("ikev2:req:reject:"))
async def ikev2_req_reject(cb: CallbackQuery):
    if not await is_admin(cb.from_user.id):
        return

    parts = cb.data.split(":")
    req_id = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else None

    req = await get_ikev2_request(req_id)
    if not req:
        await cb.answer("Заявка не найдена", show_alert=True)
        return
    if req["status"] != "pending":
        await cb.answer("Заявка уже обработана", show_alert=True)
        return

    await decide_ikev2_request(req_id, decided_by=cb.from_user.id, status="rejected")

    try:
        await cb.bot.send_message(req["tg_user"], "❌ Заявка на IKEv2 отклонена администратором.")
    except Exception:
        pass

    await cb.answer("Отклонено")
    if page is not None:
        await cb.message.edit_text("❌ Отклонено.", reply_markup=_kb_admin_back())
    else:
        await cb.message.edit_text("❌ Отклонено.", reply_markup=_kb_admin_back())


def _kb_account_actions(acc_id: int, page: int, status: str) -> InlineKeyboardMarkup:
    rows = []
    if status == "active":
        rows.append([
            InlineKeyboardButton(text="🔁 Сбросить пароль и прислать профиль", callback_data=f"ikev2:acc:reset:{acc_id}:{page}"),
        ])
        rows.append([
            InlineKeyboardButton(text="⛔️ Отключить", callback_data=f"ikev2:acc:revoke:{acc_id}:{page}"),
        ])
    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"ikev2:list:active:{page}" if status == "active" else f"ikev2:list:revoked:{page}",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@rt.callback_query(F.data.startswith("ikev2:list:active:"))
async def ikev2_active_list(cb: CallbackQuery):
    if not await is_admin(cb.from_user.id):
        return

    _, _, _, page_s = cb.data.split(":", 3)
    page = max(1, int(page_s))
    offset = (page - 1) * PAGE_SIZE

    rows = await list_ikev2_accounts(status="active", limit=PAGE_SIZE, offset=offset)
    if not rows:
        await cb.message.edit_text("🔐 IKEv2: активных профилей нет.", reply_markup=_kb_admin_back())
        await cb.answer()
        return

    lines = ["<b>🔐 IKEv2: активные профили</b>"]
    kb_rows = []
    for r in rows:
        lines.append(f"• #{r['id']} • {r['device_name']} • tg:{r['tg_user']}")
        kb_rows.append([InlineKeyboardButton(text=f"Открыть #{r['id']}", callback_data=f"ikev2:acc:open:{r['id']}:{page}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"ikev2:list:active:{page-1}"))
    if len(rows) == PAGE_SIZE:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"ikev2:list:active:{page+1}"))
    if nav:
        kb_rows.append(nav)

    kb_rows.append([InlineKeyboardButton(text="⬅️ Админ-меню", callback_data="admin:back")])

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )
    await cb.answer()


@rt.callback_query(F.data.startswith("ikev2:list:revoked:"))
async def ikev2_revoked_list(cb: CallbackQuery):
    if not await is_admin(cb.from_user.id):
        return

    _, _, _, page_s = cb.data.split(":", 3)
    page = max(1, int(page_s))
    offset = (page - 1) * PAGE_SIZE

    rows = await list_ikev2_accounts(status="revoked", limit=PAGE_SIZE, offset=offset)
    if not rows:
        await cb.message.edit_text("🔐 IKEv2: отключённых профилей нет.", reply_markup=_kb_admin_back())
        await cb.answer()
        return

    lines = ["<b>🔐 IKEv2: отключённые профили</b>"]
    kb_rows = []
    for r in rows:
        lines.append(f"• #{r['id']} • {r['device_name']} • tg:{r['tg_user']}")
        kb_rows.append([InlineKeyboardButton(text=f"Открыть #{r['id']}", callback_data=f"ikev2:acc:open:{r['id']}:{page}")])

    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"ikev2:list:revoked:{page-1}"))
    if len(rows) == PAGE_SIZE:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"ikev2:list:revoked:{page+1}"))
    if nav:
        kb_rows.append(nav)

    kb_rows.append([InlineKeyboardButton(text="⬅️ Админ-меню", callback_data="admin:back")])

    await cb.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
        parse_mode="HTML",
    )
    await cb.answer()


@rt.callback_query(F.data.startswith("ikev2:acc:open:"))
async def ikev2_acc_open(cb: CallbackQuery):
    if not await is_admin(cb.from_user.id):
        return

    parts = cb.data.split(":")
    acc_id = int(parts[3])
    page = int(parts[4]) if len(parts) > 4 else 1

    acc = await get_ikev2_account(acc_id)
    if not acc:
        await cb.answer("Профиль не найден", show_alert=True)
        return

    txt = (
        "<b>🔐 IKEv2 профиль</b>\n"
        f"ID: #{acc['id']}\n"
        f"Пользователь: tg:{acc['tg_user']}\n"
        f"Устройство: {acc['device_name']}\n"
        f"Платформа: {acc['platform']}\n"
        f"Логин: <code>{acc['username']}</code>\n"
        f"Статус: {acc['status']}\n"
        f"Создано: {acc['created_at']}"
    )
    await cb.message.edit_text(txt, reply_markup=_kb_account_actions(acc_id, page, acc["status"]), parse_mode="HTML")
    await cb.answer()


@rt.callback_query(F.data.startswith("ikev2:acc:revoke:"))
async def ikev2_acc_revoke(cb: CallbackQuery):
    if not await is_admin(cb.from_user.id):
        return

    _, _, _, acc_id_s, page_s = cb.data.split(":", 4)
    acc_id, page = int(acc_id_s), int(page_s)

    acc = await get_ikev2_account(acc_id)
    if not acc:
        await cb.answer("Профиль не найден", show_alert=True)
        return
    if acc["status"] != "active":
        await cb.answer("Уже отключён", show_alert=True)
        return

    await revoke_user_on_server(acc["username"])
    await revoke_ikev2_account(acc_id)

    try:
        await cb.bot.send_message(acc["tg_user"], "⛔️ Ваш IKEv2 профиль отключён администратором.")
    except Exception:
        pass

    await cb.answer("Отключено")
    await ikev2_active_list(
        CallbackQuery(
            id=cb.id,
            from_user=cb.from_user,
            chat_instance=cb.chat_instance,
            message=cb.message,
            data=f"ikev2:list:active:{page}",
        )
    )


@rt.callback_query(F.data.startswith("ikev2:acc:reset:"))
async def ikev2_acc_reset_and_send(cb: CallbackQuery):
    if not await is_admin(cb.from_user.id):
        return

    _, _, _, acc_id_s, page_s = cb.data.split(":", 4)
    acc_id, page = int(acc_id_s), int(page_s)

    acc = await get_ikev2_account(acc_id)
    if not acc:
        await cb.answer("Профиль не найден", show_alert=True)
        return
    if acc["status"] != "active":
        await cb.answer("Профиль отключён", show_alert=True)
        return

    new_pass = generate_password()
    await ensure_user_on_server(acc["username"], new_pass)
    await _send_profile(cb.bot, acc["tg_user"], acc["platform"], acc["device_name"], acc["username"], new_pass)

    await cb.answer("Отправлено")
    await ikev2_active_list(
        CallbackQuery(
            id=cb.id,
            from_user=cb.from_user,
            chat_instance=cb.chat_instance,
            message=cb.message,
            data=f"ikev2:list:active:{page}",
        )
    )
