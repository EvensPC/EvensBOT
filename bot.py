import asyncio
import logging
import os
import re

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import BotCommand, CallbackQuery, Message

import config
import db
import keyboards
from catalog import Catalog, fetch_xlsx_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

catalog = Catalog()
bot: Bot
dp = Dispatcher()

menu_msg: dict[int, int] = {}           # chat_id -> id постоянного главного меню
temp_msg: dict[int, int] = {}           # chat_id -> id текущего временного сообщения
temp_tasks: dict[int, asyncio.Task] = {}  # chat_id -> таймер автоудаления

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


def _cancel_temp_timer(chat_id: int):
    task = temp_tasks.pop(chat_id, None)
    if task:
        task.cancel()


def _arm_temp_timer(chat_id: int, message_id: int):
    _cancel_temp_timer(chat_id)

    async def _run():
        try:
            await asyncio.sleep(config.TEMP_MSG_TTL)
            if temp_msg.get(chat_id) == message_id:
                temp_msg.pop(chat_id, None)
                await delete_message(chat_id, message_id)
        except asyncio.CancelledError:
            pass
        finally:
            temp_tasks.pop(chat_id, None)

    temp_tasks[chat_id] = asyncio.create_task(_run())


async def _delete_temp(chat_id: int):
    _cancel_temp_timer(chat_id)
    mid = temp_msg.pop(chat_id, None)
    if mid:
        await delete_message(chat_id, mid)


async def show_menu(chat_id: int, text: str):
    """Постоянное главное меню (не удаляется автоматически)."""
    markup = keyboards.main_menu(catalog, chat_id == config.ADMIN_ID).as_markup()
    mid = menu_msg.get(chat_id)
    if mid is not None:
        try:
            await bot.edit_message_text(text, chat_id=chat_id, message_id=mid, reply_markup=markup)
            return
        except Exception:
            pass
    msg = await bot.send_message(chat_id, text, reply_markup=markup)
    menu_msg[chat_id] = msg.message_id


async def open_temp(chat_id: int, text: str, markup=None):
    """Отправляет временное сообщение (автоудаление через TTL)."""
    await _delete_temp(chat_id)
    msg = await bot.send_message(chat_id, text, reply_markup=markup)
    temp_msg[chat_id] = msg.message_id
    _arm_temp_timer(chat_id, msg.message_id)


async def go_menu(chat_id: int, text: str = "Главное меню:"):
    await _delete_temp(chat_id)
    await show_menu(chat_id, text)


async def render_window(call: CallbackQuery, text: str, markup=None):
    """Редактирует временное окно в месте, либо открывает новое (если клик был по главному меню)."""
    chat_id = call.message.chat.id
    if call.message.message_id != menu_msg.get(chat_id):
        try:
            await call.message.edit_text(text, reply_markup=markup)
            temp_msg[chat_id] = call.message.message_id
            _arm_temp_timer(chat_id, call.message.message_id)
            return
        except Exception:
            pass
    await open_temp(chat_id, text, markup)


def admin_panel_data():
    requests = db.pending_requests()
    if not requests:
        return "Админ-панель\n\nНет заявок на оптовый доступ.", keyboards.admin_menu().as_markup()
    return "Админ-панель\n\nЗаявки на оптовый доступ:", keyboards.admin_panel(requests).as_markup()


def price_for(user: dict, wholesale_price: int) -> int:
    if user and user.get("role") == "wholesaler":
        return wholesale_price
    return round(wholesale_price * config.RETAIL_MARKUP)


def fmt_price(value: int) -> str:
    return f"{value:,}".replace(",", " ")


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
        catalog.rebuild(data)
        logger.info("Каталог обновлён: %d корневых категорий, %d товаров", len(catalog.roots), len(catalog.all_products))
    except Exception as e:
        logger.error("Не удалось обновить каталог: %s", e)


async def catalog_updater():
    while True:
        await asyncio.sleep(config.UPDATE_INTERVAL_SECONDS)
        await refresh_catalog()


# ---------------- Команды ----------------

@dp.message(Command("start"))
async def cmd_start(message: Message):
    user = await ensure_user(message)
    role_name = "оптовик" if user.get("role") == "wholesaler" else "покупатель"
    await delete_message(message.chat.id, message.message_id)
    await show_menu(
        message.chat.id,
        f"🛒 Каталог товаров\n\n"
        f"Добро пожаловать, {message.from_user.first_name}! Ваш статус: {role_name}.\n"
        "Выберите категорию или воспользуйтесь поиском.",
    )


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await ensure_user(message)
    await delete_message(message.chat.id, message.message_id)
    await go_menu(message.chat.id)


@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    user = await ensure_user(message)
    role_name = "Оптовый покупатель" if user.get("role") == "wholesaler" else "Розничный покупатель"
    await delete_message(message.chat.id, message.message_id)
    await open_temp(
        message.chat.id,
        f"👤 Ваш профиль\n\n"
        f"ID: {user['user_id']}\nИмя: {user['full_name'] or '-'}\nСтатус: {role_name}",
        keyboards.profile_menu().as_markup(),
    )


@dp.message(Command("sync"))
async def cmd_sync(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await delete_message(message.chat.id, message.message_id)
        await open_temp(message.chat.id, "⛔️ Нет доступа.")
        return
    await delete_message(message.chat.id, message.message_id)
    await open_temp(message.chat.id, "🔄 Обновляю каталог из Google Sheets…")
    await refresh_catalog()
    text = f"✅ Готово. Категорий: {len(catalog.roots)}, товаров: {len(catalog.all_products)}."
    mid = temp_msg.get(message.chat.id)
    if mid:
        try:
            await bot.edit_message_text(text, chat_id=message.chat.id, message_id=mid)
            _arm_temp_timer(message.chat.id, mid)
            return
        except Exception:
            pass
    await open_temp(message.chat.id, text)


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await delete_message(message.chat.id, message.message_id)
        await open_temp(message.chat.id, "⛔️ Нет доступа.")
        return
    await delete_message(message.chat.id, message.message_id)
    text, markup = admin_panel_data()
    await open_temp(message.chat.id, text, markup)


@dp.message(Command("wholesale"))
async def cmd_wholesale(message: Message):
    user = await ensure_user(message)
    await delete_message(message.chat.id, message.message_id)
    if user.get("role") == "wholesaler":
        await open_temp(message.chat.id, "✅ Вы уже оптовик.")
        return
    if user.get("opt_requested"):
        await open_temp(message.chat.id, "⏳ Ваша заявка уже отправлена и ожидает подтверждения.")
        return
    await open_temp(
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
        await open_temp(
            message.chat.id,
            f"🔍 По запросу «{query}» ничего не найдено.\nПопробуйте изменить запрос.",
        )
        return
    await show_search_results(call=None, chat_id=message.chat.id, results=results, query=query, user=user)


async def show_search_results(call, chat_id, results, query, user, page: int = 0):
    start = page * config.SEARCH_PER_PAGE
    chunk = results[start : start + config.SEARCH_PER_PAGE]
    lines = [f"🔍 Результаты по запросу «{query}»:", f"Найдено: {len(results)}", ""]
    for i, p in enumerate(chunk, start=start + 1):
        price = price_for(user, p.price)
        lines.append(f"{i}. {p.name}\n   💰 {fmt_price(price)} ₽")
    markup = keyboards.search_results(results, page, config.SEARCH_PER_PAGE, query)
    if call is not None:
        await render_window(call, "\n".join(lines), markup.as_markup())
    else:
        await open_temp(chat_id, "\n".join(lines), markup.as_markup())


# ---------------- Навигация по каталогу ----------------

@dp.callback_query(F.data.startswith("cmd:"))
async def cb_menu(call: CallbackQuery):
    cmd = call.data.split(":", 1)[1]
    if cmd == "menu":
        await go_menu(call.message.chat.id)
    elif cmd == "admin":
        if call.from_user.id != config.ADMIN_ID:
            await call.answer("⛔️ Нет доступа", show_alert=True)
            return
        text, markup = admin_panel_data()
        await render_window(call, text, markup)
    elif cmd == "search":
        await render_window(call, "🔍 Введите название товара\n(например: 5060, RTX 4060, i5-12400):")
    elif cmd == "profile":
        user = db.get_user(call.from_user.id)
        role_name = "Оптовый покупатель" if user and user["role"] == "wholesaler" else "Розничный покупатель"
        await render_window(
            call,
            f"👤 Ваш профиль\n\n"
            f"ID: {call.from_user.id}\nИмя: {user['full_name'] if user else '-'}\nСтатус: {role_name}",
            keyboards.profile_menu().as_markup(),
        )
    elif cmd == "wholesale":
        user = db.get_user(call.from_user.id)
        if user and user["role"] == "wholesaler":
            await render_window(call, "✅ Вы уже оптовик.")
            return
        if user and user["opt_requested"]:
            await render_window(call, "⏳ Ваша заявка уже отправлена и ожидает подтверждения.")
            return
        await render_window(
            call,
            "🤝 Хотите получить оптовый доступ?\nОтправьте заявку, администратор её рассмотрит.",
            keyboards.wholesale_menu().as_markup(),
        )
    elif cmd == "request_opt":
        db.request_opt(call.from_user.id)
        await render_window(call, "📨 Заявка на оптовый доступ отправлена. Ожидайте подтверждения.")
        if config.ADMIN_ID:
            await safe_send(
                config.ADMIN_ID,
                f"🆕 Новая заявка на оптовый доступ!\n"
                f"Пользователь: @{call.from_user.username or '-'}\n"
                f"Имя: {call.from_user.full_name or '-'}\nID: {call.from_user.id}",
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
        text = f"📁 {cat.path}\n\nВыберите подкатегорию:"
        markup = keyboards.category_menu(cat)
        await render_window(call, text, markup.as_markup())
    elif cat.products:
        await show_products(call, cat)
    else:
        await call.answer("Здесь пока нет товаров", show_alert=True)
        return
    await call.answer()


async def show_products(call: CallbackQuery, cat, page: int = 0):
    user = db.get_user(call.from_user.id)
    total = len(cat.products)
    start = page * config.PRICE_PER_PAGE
    chunk = cat.products[start : start + config.PRICE_PER_PAGE]
    lines = [f"📁 {cat.path}", f"Товаров: {total}", ""]
    for i, p in enumerate(chunk, start=start + 1):
        price = price_for(user, p.price)
        lines.append(f"{i}. {p.name}\n   💰 {fmt_price(price)} ₽")
    markup = keyboards.products_menu(cat, page, config.PRICE_PER_PAGE)
    await render_window(call, "\n".join(lines), markup.as_markup())


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


@dp.callback_query(F.data.startswith("prod:"))
async def cb_product(call: CallbackQuery):
    code = call.data.split(":", 1)[1]
    user = db.get_user(call.from_user.id)
    product = None
    for p in catalog.all_products:
        if p.code == code:
            product = p
            break
    if product is None:
        await call.answer("Товар не найден", show_alert=True)
        return
    price = price_for(user, product.price)
    text = (
        f"📦 {product.name}\n\n"
        f"Артикул: {product.code}\n"
        f"Цена: {fmt_price(price)} ₽"
    )
    kb = keyboards.InlineKeyboardBuilder()
    kb.row(keyboards.InlineKeyboardButton(text="🏠 В меню", callback_data="cmd:menu"))
    await render_window(call, text, kb.as_markup())
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
    await show_search_results(call=call, chat_id=call.message.chat.id, results=results, query=query, user=user, page=page)
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
        await call.answer("Пользователь не найден", show_alert=True)
        return
    if action == "approve":
        db.set_role(user_id, "wholesaler")
        await safe_send(
            user_id,
            "✅ Поздравляем! Ваш оптовый доступ подтверждён.\nТеперь цены отображаются по прайсу.",
        )
    elif action == "reject":
        db.set_role(user_id, "buyer")
        db.clear_opt_request(user_id)
        await safe_send(user_id, "К сожалению, ваша заявка на оптовый доступ была отклонена.")
    text, markup = admin_panel_data()
    await render_window(call, text, markup)
    await call.answer()


@dp.callback_query(F.data.startswith("admin:list"))
async def cb_admin_list(call: CallbackQuery):
    if call.from_user.id != config.ADMIN_ID:
        await call.answer("⛔️ Нет доступа", show_alert=True)
        return
    users = db.wholesalers()
    if not users:
        await render_window(call, "Оптовиков пока нет.", keyboards.wholesalers_menu().as_markup())
    else:
        lines = [f"🧾 Оптовики ({len(users)}):", ""]
        for u in users:
            name = u["full_name"] or u["username"] or "-"
            uname = f"@{u['username']}" if u["username"] else ""
            lines.append(f"• {name} {uname}\n  ID: {u['user_id']}")
        await render_window(call, "\n".join(lines), keyboards.wholesalers_menu().as_markup())
    await call.answer()


async def on_startup():
    logger.info("БД: %s", "PostgreSQL (DATABASE_URL)" if os.getenv("DATABASE_URL") else "SQLite (файл data/bot.db)")
    await refresh_catalog()
    asyncio.create_task(catalog_updater())


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


async def main():
    global bot
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN не задан. Создайте файл .env рядом с ботом или укажите переменные окружения.")
        return
    bot = Bot(token=config.BOT_TOKEN)
    await bot.set_my_commands(BOT_COMMANDS)
    dp.startup.register(on_startup)
    await asyncio.gather(_run_http_server(), dp.start_polling(bot))


if __name__ == "__main__":
    asyncio.run(main())