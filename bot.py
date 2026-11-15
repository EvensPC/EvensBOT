import asyncio
import logging
import os
import re
from html import escape

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.types import BotCommand, CallbackQuery, Message

import config
import db
import keyboards
from catalog import Catalog, fetch_xlsx_bytes
from price482 import Price482
from price482 import fetch_xlsx_bytes as fetch_price482_bytes
from pricing import fmt_price, price_for

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

catalog = Catalog()
price482 = Price482()
bot: Bot
dp = Dispatcher()

ui_msg: dict[int, int] = {}  # chat_id -> id единственного сообщения бота в чате

BOT_COMMANDS = [
    BotCommand(command="start", description="Запустить бота"),
    BotCommand(command="menu", description="Главное меню"),
    BotCommand(command="profile", description="Мой профиль"),
    BotCommand(command="wholesale", description="Стать оптовиком"),
    BotCommand(command="admin", description="Админ-панель"),
]


async def delete_message(chat_id: int, message_id: int):
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass


async def render(chat_id: int, text: str, markup=None, parse_mode=None):
    """Показывает содержимое в единственном сообщении чата (правит его, либо создаёт)."""
    mid = ui_msg.get(chat_id)
    if mid is not None:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=mid, reply_markup=markup, parse_mode=parse_mode)
            return
        except Exception:
            pass
        await delete_message(chat_id, mid)
    msg = await bot.send_message(chat_id, text, reply_markup=markup, parse_mode=parse_mode)
    ui_msg[chat_id] = msg.message_id


async def render_call(call: CallbackQuery, text: str, markup=None, parse_mode=None):
    """Редактирует сообщение, на котором нажата кнопка (оно же — единственное сообщение чата)."""
    try:
        await call.message.edit_text(text, reply_markup=markup, parse_mode=parse_mode)
    except Exception:
        mid = ui_msg.get(call.message.chat.id)
        if mid:
            await delete_message(call.message.chat.id, mid)
        msg = await bot.send_message(call.message.chat.id, text, reply_markup=markup, parse_mode=parse_mode)
        ui_msg[call.message.chat.id] = msg.message_id
        return
    ui_msg[call.message.chat.id] = call.message.message_id


async def show_main_menu(chat_id: int, text: str = "Главное меню:"):
    await render(chat_id, text, keyboards.main_menu(catalog, chat_id == config.ADMIN_ID).as_markup())


def admin_panel_data():
    requests = db.pending_requests()
    if not requests:
        return "⚙️ Админ-панель\n\nНет заявок на оптовый доступ.", keyboards.admin_menu().as_markup()
    return "⚙️ Админ-панель\n\nЗаявки на оптовый доступ:", keyboards.admin_panel(requests).as_markup()


async def ensure_user(message: Message) -> dict:
    return db.upsert_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.full_name or "",
    )


async def safe_send(chat_id, text, reply_markup=None):
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except Exception as e:
        logger.warning("send to %s failed: %s", chat_id, e)


async def refresh_catalog():
    try:
        data = fetch_xlsx_bytes()
    except Exception as e:
        logger.error("Не удалось скачать каталог: %s", e)
        return
    try:
        price482.rebuild(fetch_price482_bytes())
    except Exception as e:
        logger.error("Не удалось обновить прайс 482 (остаются прежние данные): %s", e)
    try:
        catalog.rebuild(data, price482)
    except Exception as e:
        logger.error("Не удалось обновить каталог: %s", e)
        return
    logger.info(
        "Каталог обновлён: %d корневых категорий, %d товаров, %d с ценой 482",
        len(catalog.roots),
        len(catalog.all_products),
        sum(1 for p in catalog.all_products if p.price482 is not None),
    )


async def catalog_updater():
    while True:
        await asyncio.sleep(config.UPDATE_INTERVAL_SECONDS)
        await refresh_catalog()


async def wholesalers_file_checker():
    while True:
        await asyncio.sleep(config.WHOLESALERS_CHECK_SECONDS)
        try:
            granted = db.sync_wholesalers_from_file(str(config.WHOLESALERS_FILE))
            for uid in granted:
                await safe_send(
                    uid,
                    "✅ Ваш оптовый доступ подтверждён.\nТеперь цены отображаются по прайсу.",
                )
                logger.info("Оптовый доступ выдан по файлу: %s", uid)
        except Exception as e:
            logger.error("Ошибка проверки файла оптовиков: %s", e)


# ---------------- Команды ----------------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = await ensure_user(message)
    role_name = "оптовик" if user.get("role") == "wholesaler" else "покупатель"
    await delete_message(message.chat.id, message.message_id)
    await show_main_menu(
        message.chat.id,
        f"🛒 Каталог товаров\n\n"
        f"Добро пожаловать, {message.from_user.first_name}! Ваш статус: {role_name}.\n"
        "Выберите категорию или воспользуйтесь поиском.",
    )


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await ensure_user(message)
    await delete_message(message.chat.id, message.message_id)
    await show_main_menu(message.chat.id)


@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    user = await ensure_user(message)
    role_name = "Оптовый покупатель" if user.get("role") == "wholesaler" else "Розничный покупатель"
    await delete_message(message.chat.id, message.message_id)
    await render(
        message.chat.id,
        f"👤 Ваш профиль\n\n"
        f"ID: {user['user_id']}\nИмя: {user['full_name'] or '-'}\nСтатус: {role_name}",
        keyboards.profile_menu().as_markup(),
    )


@dp.message(Command("sync"))
async def cmd_sync(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await delete_message(message.chat.id, message.message_id)
        await render(message.chat.id, "⛔️ Нет доступа.")
        return
    await delete_message(message.chat.id, message.message_id)
    await render(message.chat.id, "🔄 Обновляю каталог из Google Sheets…")
    await refresh_catalog()
    matched = sum(1 for p in catalog.all_products if p.price482 is not None)
    await render(
        message.chat.id,
        f"✅ Готово. Категорий: {len(catalog.roots)}, товаров: {len(catalog.all_products)}, "
        f"с ценой 482: {matched}.",
    )


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await delete_message(message.chat.id, message.message_id)
        await render(message.chat.id, "⛔️ Нет доступа.")
        return
    await delete_message(message.chat.id, message.message_id)
    text, markup = admin_panel_data()
    await render(message.chat.id, text, markup)


@dp.message(Command("wholesale"))
async def cmd_wholesale(message: Message):
    user = await ensure_user(message)
    await delete_message(message.chat.id, message.message_id)
    if user.get("role") == "wholesaler":
        await render(message.chat.id, "✅ Вы уже оптовик.")
        return
    if user.get("opt_requested"):
        await render(message.chat.id, "⏳ Ваша заявка уже отправлена и ожидает подтверждения.")
        return
    await render(
        message.chat.id,
        "🤝 Хотите получить оптовый доступ?\nОтправьте заявку, администратор её рассмотрит.",
        keyboards.wholesale_menu().as_markup(),
    )


# ---------------- Поиск ----------------

def search_products(query: str, limit: int = 200):
    q = re.sub(r"\s+", " ", query.strip().lower())
    results = []
    for p in catalog.all_products:
        if q in p.name.lower() or q in p.code.lower():
            results.append(p)
            if len(results) >= limit:
                break
    return results


@dp.message(F.text)
async def on_text(message: Message):
    if message.text.startswith("/"):
        return
    user = await ensure_user(message)
    query = message.text.strip()
    if not query:
        return
    await delete_message(message.chat.id, message.message_id)
    results = search_products(query)
    if not results:
        await render(
            message.chat.id,
            f"🔍 По запросу «{query}» ничего не найдено.\nПопробуйте изменить запрос.",
        )
        return
    await show_search_results(message.chat.id, results, query, user)


async def show_search_results(chat_id, results, query, user, page: int = 0):
    markup = keyboards.search_results(results, page, config.SEARCH_PER_PAGE, query, user)
    total = len(results)
    start = page * config.SEARCH_PER_PAGE
    end = min(start + config.SEARCH_PER_PAGE, total)
    text = f"🔍 Результаты по запросу «{query}»\nНайдено: {total}"
    if start < total:
        text += f"\nСтраница {page + 1}/{max(1, (total + config.SEARCH_PER_PAGE - 1) // config.SEARCH_PER_PAGE)}"
    await render(chat_id, text, markup.as_markup())


# ---------------- Навигация по каталогу ----------------

@dp.callback_query(F.data.startswith("cmd:"))
async def cb_menu(call: CallbackQuery):
    cmd = call.data.split(":", 1)[1]
    if cmd == "menu":
        # Редактируем текущее сообщение, а не создаём новое
        await render_call(
            call,
            "Главное меню:",
            keyboards.main_menu(catalog, call.from_user.id == config.ADMIN_ID).as_markup(),
        )
    elif cmd == "admin":
        if call.from_user.id != config.ADMIN_ID:
            await call.answer("⛔️ Нет доступа", show_alert=True)
            return
        text, markup = admin_panel_data()
        await render_call(call, text, markup)
    elif cmd == "search":
        await render_call(call, "🔍 Введите название товара\n(например: 5060, RTX 4060, i5-12400):")
    elif cmd == "profile":
        user = db.get_user(call.from_user.id)
        role_name = "Оптовый покупатель" if user and user["role"] == "wholesaler" else "Розничный покупатель"
        await render_call(
            call,
            f"👤 Ваш профиль\n\n"
            f"ID: {call.from_user.id}\nИмя: {user['full_name'] if user else '-'}\nСтатус: {role_name}",
            keyboards.profile_menu().as_markup(),
        )
    elif cmd == "wholesale":
        user = db.get_user(call.from_user.id)
        if user and user["role"] == "wholesaler":
            await render_call(call, "✅ Вы уже оптовик.")
            return
        if user and user["opt_requested"]:
            await render_call(call, "⏳ Ваша заявка уже отправлена и ожидает подтверждения.")
            return
        await render_call(
            call,
            "🤝 Хотите получить оптовый доступ?\nОтправьте заявку, администратор её рассмотрит.",
            keyboards.wholesale_menu().as_markup(),
        )
    elif cmd == "request_opt":
        db.request_opt(call.from_user.id)
        await render_call(call, "📨 Заявка на оптовый доступ отправлена. Ожидайте подтверждения.")
        if config.ADMIN_ID:
            uid = call.from_user.id
            kb = keyboards.InlineKeyboardBuilder()
            kb.row(
                keyboards.InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"opt:approve:{uid}"),
                keyboards.InlineKeyboardButton(text="❌ Отклонить", callback_data=f"opt:reject:{uid}"),
            )
            await safe_send(
                config.ADMIN_ID,
                f"🆕 Новая заявка на оптовый доступ!\n"
                f"Пользователь: @{call.from_user.username or '-'}\n"
                f"Имя: {call.from_user.full_name or '-'}\nID: {call.from_user.id}",
                kb.as_markup(),
            )
    await call.answer()


@dp.callback_query(F.data.startswith("cat:"))
async def cb_category(call: CallbackQuery):
    key = call.data.split(":", 1)[1]
    cat = catalog.get(key)
    if cat is None:
        await call.answer("Категория не найдена", show_alert=True)
        return
    if cat.children:
        await render_call(
            call,
            f"📁 {cat.path}\n\nВыберите подкатегорию:",
            keyboards.category_menu(cat).as_markup(),
        )
    elif cat.products:
        await show_products(call, cat)
    else:
        await call.answer("Здесь пока нет товаров", show_alert=True)
        return
    await call.answer()


async def show_products(call: CallbackQuery, cat, page: int = 0):
    user = db.get_user(call.from_user.id)
    total = len(cat.products)

    if not total:
        await call.answer("Здесь пока нет товаров", show_alert=True)
        return

    per_page = config.PRICE_PER_PAGE
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    chunk = cat.products[page * per_page : (page + 1) * per_page]

    text = f"🎮 <b>{escape(cat.path)}</b> · {total}"
    if pages > 1:
        text += f"\nСтраница {page + 1}/{pages}"
    text += "\n\nВыберите товар:"
    markup = keyboards.products_menu(cat, chunk, page, per_page, user)
    await render_call(call, text, markup.as_markup(), parse_mode="HTML")


@dp.callback_query(F.data.startswith("pg:"))
async def cb_page(call: CallbackQuery):
    _, key, page = call.data.split(":")
    cat = catalog.get(key)
    if cat is None:
        await call.answer("Категория не найдена", show_alert=True)
        return
    await show_products(call, cat, int(page))
    await call.answer()


@dp.callback_query(F.data.startswith("info:"))
async def cb_info(call: CallbackQuery):
    await call.answer()


def _signed_price(value: int) -> str:
    if value > 0:
        return f"+{fmt_price(value)}"
    if value < 0:
        return f"−{fmt_price(-value)}"
    return "0"


def _signed_percent(value: float) -> str:
    text = f"{abs(value):.1f}".replace(".", ",")
    if value > 0:
        return f"+{text}%"
    if value < 0:
        return f"−{text}%"
    return "0%"


def product_card_text(product, user: dict | None) -> str:
    price = price_for(user, product.price)
    cat = catalog.category_for(product.code)

    lines = [
        f"📦 <b>{escape(product.name)}</b>",
        "",
        f"Артикул: <code>{escape(product.code)}</code>",
    ]
    if cat is not None:
        lines.append(f"Категория: {escape(cat.path)}")
    lines.append("")

    if product.price > 0:
        lines.append(f"Наша цена: <b>{fmt_price(price)}\u00A0₽</b>")
    else:
        lines.append("Наша цена: по запросу")

    item = product.price482
    if item is None:
        lines.append("Прайс 482: нет в прайсе")
        return "\n".join(lines)

    lines.append(f"📊 Прайс 482: {fmt_price(item.price)}\u00A0₽ · код {escape(item.code)}")
    if product.price > 0 and item.price > 0:
        diff = price - item.price
        percent = diff / item.price * 100
        icon = "📈" if diff > 0 else ("📉" if diff < 0 else "✅")
        lines.append(f"{icon} Разница: <b>{_signed_price(diff)}\u00A0₽</b> ({_signed_percent(percent)})")
    lines.append(f"\n<i>В прайсе 482: {escape(item.name)}</i>")
    return "\n".join(lines)


@dp.callback_query(F.data.startswith("prod:"))
async def cb_product(call: CallbackQuery):
    parts = call.data.split(":")
    code = parts[1]
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    user = db.get_user(call.from_user.id)
    product = catalog.find_product(code)
    if product is None:
        await call.answer("Товар не найден", show_alert=True)
        return
    cat = catalog.category_for(product.code)
    if cat is None:
        back_cb = "cmd:menu"
    elif page > 0:
        back_cb = f"pg:{cat.key}:{page}"
    else:
        back_cb = f"cat:{cat.key}"
    await render_call(
        call,
        product_card_text(product, user),
        keyboards.product_card(product, back_cb).as_markup(),
        parse_mode="HTML",
    )
    await call.answer()


@dp.callback_query(F.data.startswith("spg:"))
async def cb_search_page(call: CallbackQuery):
    parts = call.data.split(":")
    page = int(parts[1])
    query = parts[2]
    user = db.get_user(call.from_user.id)
    results = search_products(query)
    if not results:
        await call.answer("Ничего не найдено", show_alert=True)
        return
    await show_search_results(call.message.chat.id, results, query, user, page)
    await call.answer()


# ---------------- Оптовые заявки (для админа) ----------------

@dp.callback_query(F.data.startswith("opt:"))
async def cb_opt(call: CallbackQuery):
    if call.from_user.id != config.ADMIN_ID:
        await call.answer("⛔️ Нет доступа", show_alert=True)
        return
    _, action, user_id = call.data.split(":")
    user_id = int(user_id)
    user = db.get_user(user_id)
    if user is None:
        # Записи нет в этой БД (устаревшая кнопка / пересоздана база) — создаём запись и продолжаем.
        logger.warning("opt:%s: пользователя %s нет в БД, создаю запись автоматически", action, user_id)
        db.upsert_user(user_id, "", "")
    if action == "approve":
        db.set_role(user_id, "wholesaler")
        await safe_send(
            user_id,
            "✅ Поздравляем! Ваш оптовый доступ подтверждён.\nТеперь цены отображаются по прайсу.",
        )
        await call.answer("Заявка подтверждена")
    elif action == "reject":
        db.set_role(user_id, "buyer")
        db.clear_opt_request(user_id)
        await safe_send(user_id, "К сожалению, ваша заявка на оптовый доступ была отклонена.")
    text, markup = admin_panel_data()
    await render_call(call, text, markup)
    await call.answer()


@dp.callback_query(F.data.startswith("admin:list"))
async def cb_admin_list(call: CallbackQuery):
    if call.from_user.id != config.ADMIN_ID:
        await call.answer("⛔️ Нет доступа", show_alert=True)
        return
    users = db.wholesalers()
    if not users:
        await render_call(call, "Оптовиков пока нет.", keyboards.wholesalers_menu().as_markup())
    else:
        lines = [f"🧾 Оптовики ({len(users)}):", ""]
        for u in users:
            name = u["full_name"] or u["username"] or "-"
            uname = f"@{u['username']}" if u["username"] else ""
            lines.append(f"• {name} {uname}\n  ID: {u['user_id']}")
        await render_call(call, "\n".join(lines), keyboards.wholesalers_menu().as_markup())
    await call.answer()


async def on_startup():
    logger.info("БД: %s", "PostgreSQL (DATABASE_URL)" if os.getenv("DATABASE_URL") else "SQLite (файл data/bot.db)")
    await refresh_catalog()
    asyncio.create_task(catalog_updater())
    asyncio.create_task(wholesalers_file_checker())


async def _health(request):
    return web.Response(text="OK")


async def _run_http_server():
    app = web.Application()
    app.router.add_get("/", _health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "8000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info("HTTP-сервер запущен на порту %s", port)


async def _run_polling():
    """Ждёт появления связи с Telegram и запускает polling (не падая при старте без сети)."""
    while True:
        try:
            await bot.get_me()
            break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("Telegram API недоступен: %s. Повтор через 30 с…", e)
            await asyncio.sleep(30)
    await dp.start_polling(bot)


async def main():
    global bot
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN не задан. Создайте файл .env рядом с ботом или укажите переменные окружения.")
        return
    session = AiohttpSession(proxy=config.PROXY_URL) if config.PROXY_URL else None
    if session is not None:
        logger.info("Для Telegram API используется прокси из PROXY_URL")
    bot = Bot(token=config.BOT_TOKEN, session=session)
    try:
        await bot.set_my_commands(BOT_COMMANDS)
    except Exception as e:
        logger.warning("Не удалось установить команды бота (нет связи с Telegram?): %s", e)
    dp.startup.register(on_startup)
    await asyncio.gather(_run_http_server(), _run_polling())


if __name__ == "__main__":
    asyncio.run(main())