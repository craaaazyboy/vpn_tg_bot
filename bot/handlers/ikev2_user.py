from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.fsm.context import FSMContext

from settings import settings
from state import RequestIkev2
from keyboards import kb_user_main, kb_ikev2_platform
from db import (
    upsert_user,
    is_admin,
    create_ikev2_request,
    list_ikev2_accounts_by_user,
    get_ikev2_account_owned_by,
)
from services.ikev2 import (
    generate_username,
    generate_password,
    ensure_user_on_server,
    fetch_ca_cert_der_b64,
    build_ios_mobileconfig,
    build_android_sswan,
)


rt = Router()


def _kb_admin_ikev2_request(req_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"ikev2:req:approve:{req_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"ikev2:req:reject:{req_id}"),
        ]
    ])


def _kb_my_ikev2_accounts(accounts) -> InlineKeyboardMarkup:
    rows = []
    for a in accounts:
        status = "✅" if a["status"] == "active" else "⛔️"
        rows.append([
            InlineKeyboardButton(
                text=f"{status} {a['device_name']} ({a['platform']})",
                callback_data=f"ikev2:acc:open:{a['id']}",
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ikev2:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_my_ikev2_account_actions(acc_id: int, is_active: bool) -> InlineKeyboardMarkup:
    rows = []
    if is_active:
        rows.append([InlineKeyboardButton(text="🔁 Прислать профиль (сбросит пароль)", callback_data=f"ikev2:acc:resend:{acc_id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="ikev2:my")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@rt.message(F.text.in_({"🔐 IKEv2/IPsec", "IKEv2/IPsec"}))
async def ikev2_start(msg: Message, state: FSMContext):
    await state.clear()
    await upsert_user(
        msg.from_user.id,
        msg.from_user.username,
        msg.from_user.first_name,
        msg.from_user.last_name,
    )
    text = (
        "<b>IKEv2/IPsec</b>\n\n"
        "Выберите платформу. Дальше я попрошу название устройства (как вам удобно).\n"
        "После одобрения админом вы получите готовый профиль для установки."
    )
    await msg.answer(text, reply_markup=kb_ikev2_platform(), parse_mode="HTML")


@rt.callback_query(F.data == "ikev2:back")
async def ikev2_back(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    admin = await is_admin(cb.from_user.id)
    await cb.message.edit_text("Главное меню", reply_markup=kb_user_main(admin))
    await cb.answer()


@rt.callback_query(F.data.startswith("ikev2:platform:"))
async def ikev2_platform(cb: CallbackQuery, state: FSMContext):
    platform = cb.data.split(":", 2)[2]
    if platform not in ("ios", "android"):
        await cb.answer("Неизвестная платформа", show_alert=True)
        return
    await state.set_state(RequestIkev2.waiting_device_name)
    await state.update_data(platform=platform)
    await cb.message.edit_text(
        "Введите <b>название устройства</b> (например: <i>Ilya-iPhone</i> или <i>Samsung-A54</i>)",
        parse_mode="HTML",
    )
    await cb.answer()


@rt.message(RequestIkev2.waiting_device_name)
async def ikev2_device_name(msg: Message, state: FSMContext):
    name = (msg.text or "").strip()
    if len(name) < 2 or len(name) > 48:
        await msg.answer("Название должно быть от 2 до 48 символов. Попробуйте ещё раз.")
        return

    data = await state.get_data()
    platform = data.get("platform")
    if platform not in ("ios", "android"):
        await state.clear()
        await msg.answer("Что-то пошло не так. Начните заново: 🔐 IKEv2/IPsec")
        return

    req_id = await create_ikev2_request(msg.from_user.id, platform, name)
    await state.clear()
    await msg.answer(
        "Заявка на IKEv2 отправлена админу. Как одобрят — пришлю профиль.",
        reply_markup=kb_user_main(await is_admin(msg.from_user.id)),
    )

    # notify admins
    text = (
        "<b>Новая IKEv2 заявка</b>\n"
        f"User: @{msg.from_user.username or '—'} ({msg.from_user.id})\n"
        f"Имя: {msg.from_user.full_name}\n"
        f"Платформа: {platform}\n"
        f"Устройство: {name}\n"
        f"Request ID: {req_id}"
    )
    for admin_id in settings.ADMIN_IDS:
        try:
            await msg.bot.send_message(admin_id, text, reply_markup=_kb_admin_ikev2_request(req_id), parse_mode="HTML")
        except Exception:
            pass


@rt.message(F.text.in_({"📄 Мои профили IKEv2", "Мои профили IKEv2"}))
async def my_ikev2_profiles(msg: Message):
    await upsert_user(
        msg.from_user.id,
        msg.from_user.username,
        msg.from_user.first_name,
        msg.from_user.last_name,
    )
    accounts = await list_ikev2_accounts_by_user(msg.from_user.id)
    if not accounts:
        await msg.answer("У вас пока нет IKEv2 профилей. Нажмите: 🔐 IKEv2/IPsec")
        return
    await msg.answer("Ваши IKEv2 профили:", reply_markup=_kb_my_ikev2_accounts(accounts))


@rt.callback_query(F.data == "ikev2:my")
async def ikev2_my_back(cb: CallbackQuery):
    accounts = await list_ikev2_accounts_by_user(cb.from_user.id)
    if not accounts:
        await cb.message.edit_text("У вас пока нет IKEv2 профилей.")
    else:
        await cb.message.edit_text("Ваши IKEv2 профили:", reply_markup=_kb_my_ikev2_accounts(accounts))
    await cb.answer()


@rt.callback_query(F.data.startswith("ikev2:acc:open:"))
async def ikev2_acc_open(cb: CallbackQuery):
    acc_id = int(cb.data.split(":", 3)[3])
    acc = await get_ikev2_account_owned_by(acc_id, cb.from_user.id)
    if not acc:
        await cb.answer("Нет доступа", show_alert=True)
        return

    text = (
        "<b>IKEv2 профиль</b>\n"
        f"Устройство: {acc['device_name']}\n"
        f"Платформа: {acc['platform']}\n"
        f"Статус: {acc['status']}\n"
        f"Логин: {acc['username']}\n"
        f"Сервер: {settings.IKEV2_SERVER_ADDR}"
    )
    await cb.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=_kb_my_ikev2_account_actions(acc["id"], acc["status"] == "active"),
    )
    await cb.answer()


@rt.callback_query(F.data.startswith("ikev2:acc:resend:"))
async def ikev2_acc_resend(cb: CallbackQuery):
    acc_id = int(cb.data.split(":", 3)[3])
    acc = await get_ikev2_account_owned_by(acc_id, cb.from_user.id)
    if not acc:
        await cb.answer("Нет доступа", show_alert=True)
        return
    if acc["status"] != "active":
        await cb.answer("Профиль отключён админом", show_alert=True)
        return

    # For safety we always rotate password when resending.
    password = generate_password()
    await ensure_user_on_server(acc["username"], password)
    ca_der_b64 = await fetch_ca_cert_der_b64()

    if acc["platform"] == "ios":
        content = build_ios_mobileconfig(
            profile_name=f"IKEv2 • {acc['device_name']}",
            server_addr=settings.IKEV2_SERVER_ADDR,
            remote_id=settings.IKEV2_REMOTE_ID,
            username=acc["username"],
            password=password,
            ca_cert_der_b64=ca_der_b64,
        )
        filename = f"IKEv2-{acc['device_name']}.mobileconfig"
        await cb.bot.send_document(
            cb.from_user.id,
            BufferedInputFile(content, filename=filename),
            caption=_ios_install_text(),
            parse_mode="HTML",
        )
    else:
        content = build_android_sswan(
            profile_name=f"IKEv2 • {acc['device_name']}",
            server_addr=settings.IKEV2_SERVER_ADDR,
            remote_id=settings.IKEV2_REMOTE_ID,
            username=acc["username"],
            password=password,
            ca_cert_der_b64=ca_der_b64,
        )
        filename = f"IKEv2-{acc['device_name']}.sswan"
        await cb.bot.send_document(
            cb.from_user.id,
            BufferedInputFile(content, filename=filename),
            caption=_android_install_text(),
            parse_mode="HTML",
        )

    await cb.answer("Отправил. Пароль сброшен на новый.")


def _ios_install_text() -> str:
    return (
        "<b>Установка на iPhone (iOS)</b>\n"
        "1) Откройте файл .mobileconfig в Telegram и нажмите <b>Поделиться</b> → <b>Сохранить в файлы</b> (если нужно), либо просто <b>Открыть</b>.\n"
        "2) В <b>Настройки</b> появится пункт <b>Профиль загружен</b> — откройте и нажмите <b>Установить</b>.\n"
        "3) Включите доверие корневому сертификату: <b>Настройки</b> → <b>Основные</b> → <b>Об этом устройстве</b> → <b>Доверие сертификатам</b> → включите <b>Полное доверие</b>.\n"
        "4) Подключение: <b>Настройки</b> → <b>VPN</b>.\n\n"
        "Примечание: при каждом повторном запросе профиля пароль будет меняться."
    )


def _android_install_text() -> str:
    return (
        "<b>Установка на Android</b>\n"
        "1) Установите приложение <b>strongSwan VPN Client</b>.\n"
        "2) Откройте файл .sswan из Telegram (или сохраните и импортируйте) — приложение предложит импорт профиля.\n"
        "3) Подключитесь.\n\n"
        "Примечание: при каждом повторном запросе профиля пароль будет меняться."
    )
