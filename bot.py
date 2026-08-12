import asyncio
import logging
import os
import re

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import config
import db
import keyboards
from catalog import Catalog, fetch_xlsx_bytes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

catalog = Catalog()
bot: Bot
dp = Dispatcher()


def price_for(user: dict, wholesale_price: int) -> int:
    if user and user.get("role") == "wholesaler":
        return wholesale_price
    return round(wholesale_price * config.RETAIL_MARKUP)


def fmt_price(value: int) -> str:
    return f"{value:,}".replace(",", " ")


def product_text(product, price: int) -> str:
    return f"{product.name}\nЦена: {fmt_price(price)} ₽"


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


async def send_main_menu(chat_id, text: str):
    is_admin = chat_id == config.ADMIN_ID
    await bot.send_message(chat_id, text, reply_markup=keyboards.main_menu(catalog, is_admin).as_markup())


async def send_admin_panel(message: Message | None = None, chat_id=None, edit: bool = True):
    requests = db.pending_requests()
    if not requests:
        text = "Админ-панель\n\nНет заявок на оптовый доступ."
        markup = keyboards.admin_menu()
    else:
        text = "Админ-панель\n\nЗаявки на оптовый доступ:"
        markup = keyboards.admin_panel(requests)
    if edit and message is not None:
        await message.edit_text(text, reply_markup=markup.as_markup())
    elif chat_id is not None:
        await bot.send_message(chat_id, text, reply_markup=markup.as_markup())


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
    await send_main_menu(
        message.chat.id,
        f"Добро пожаловать, {message.from_user.first_name}!\n"
        f"Ваш статус: {role_name}\n"
        "Выберите категорию или воспользуйтесь поиском.",
    )


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await ensure_user(message)
    await send_main_menu(message.chat.id, "Главное меню:")


@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    user = await ensure_user(message)
    role_name = "Оптовый покупатель" if user.get("role") == "wholesaler" else "Розничный покупатель"
    await message.answer(
        f"Ваш профиль:\nID: {user['user_id']}\n"
        f"Имя: {user['full_name'] or '-'}\nСтатус: {role_name}",
        reply_markup=keyboards.profile_menu().as_markup(),
    )


@dp.message(Command("sync"))
async def cmd_sync(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("Нет доступа.")
        return
    await message.answer("Обновляю каталог из Google Sheets…")
    await refresh_catalog()
    await message.answer(f"Готово. Категорий: {len(catalog.roots)}, товаров: {len(catalog.all_products)}.")


@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    if message.from_user.id != config.ADMIN_ID:
        await message.answer("Нет доступа.")
        return
    await send_admin_panel(chat_id=message.chat.id, edit=False)


@dp.message(Command("wholesale"))
async def cmd_wholesale(message: Message):
    user = await ensure_user(message)
    if user.get("role") == "wholesaler":
        await message.answer("Вы уже оптовик.")
        return
    if user.get("opt_requested"):
        await message.answer("Ваша заявка уже отправлена и ожидает подтверждения.")
        return
    await message.answer(
        "Хотите получить оптовый доступ? Отправьте заявку, и администратор её рассмотрит.",
        reply_markup=keyboards.wholesale_menu().as_markup(),
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
    results = search_products(query)
    if not results:
        await message.answer(f"По запросу «{query}» ничего не найдено.\nПопробуйте изменить запрос.")
        return
    await show_search_results(message.chat.id, results, query, user)


async def show_search_results(chat_id, results, query, user, page: int = 0):
    start = page * config.SEARCH_PER_PAGE
    chunk = results[start : start + config.SEARCH_PER_PAGE]
    lines = [f"Результаты по запросу «{query}»:", ""]
    for p in chunk:
        price = price_for(user, p.price)
        lines.append(f"• {p.name}\n  {fmt_price(price)} ₽")
    kb = keyboards.search_results(results, page, config.SEARCH_PER_PAGE, query)
    await bot.send_message(chat_id, "\n".join(lines), reply_markup=kb.as_markup())


# ---------------- Навигация по каталогу ----------------

@dp.callback_query(F.data.startswith("cmd:"))
async def cb_menu(call: CallbackQuery):
    cmd = call.data.split(":", 1)[1]
    if cmd == "menu":
        is_admin = call.message.chat.id == config.ADMIN_ID
        await call.message.edit_text("Главное меню:", reply_markup=keyboards.main_menu(catalog, is_admin).as_markup())
    elif cmd == "admin":
        if call.from_user.id != config.ADMIN_ID:
            await call.answer("Нет доступа", show_alert=True)
            return
        await send_admin_panel(message=call.message, edit=True)
    elif cmd == "search":
        await call.message.edit_text("Введите название товара (например: 5060, RTX 4060, i5-12400):")
    elif cmd == "profile":
        user = db.get_user(call.from_user.id)
        role_name = "Оптовый покупатель" if user and user["role"] == "wholesaler" else "Розничный покупатель"
        await call.message.edit_text(
            f"Ваш профиль:\nID: {call.from_user.id}\nИмя: {user['full_name'] if user else '-'}\nСтатус: {role_name}",
            reply_markup=keyboards.profile_menu().as_markup(),
        )
    elif cmd == "wholesale":
        user = db.get_user(call.from_user.id)
        if user and user["role"] == "wholesaler":
            await call.message.edit_text("Вы уже оптовик.")
            return
        if user and user["opt_requested"]:
            await call.message.edit_text("Ваша заявка уже отправлена и ожидает подтверждения.")
            return
        await call.message.edit_text(
            "Хотите получить оптовый доступ? Отправьте заявку, и администратор её рассмотрит.",
            reply_markup=keyboards.wholesale_menu().as_markup(),
        )
    elif cmd == "request_opt":
        db.request_opt(call.from_user.id)
        await call.message.edit_text("Заявка на оптовый доступ отправлена. Ожидайте подтверждения.")
        if config.ADMIN_ID:
            await safe_send(
                config.ADMIN_ID,
                f"Новая заявка на оптовый доступ!\n"
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
        kb = keyboards.category_menu(cat)
        await call.message.edit_text(f"📁 {cat.path}\n\nВыберите подкатегорию:", reply_markup=kb.as_markup())
    elif cat.products:
        await show_products(call, cat)
    else:
        await call.answer("Здесь пока нет товаров", show_alert=True)
    await call.answer()


async def show_products(call: CallbackQuery, cat, page: int = 0):
    user = db.get_user(call.from_user.id)
    kb = keyboards.products_menu(cat, page, config.PRICE_PER_PAGE)
    start = page * config.PRICE_PER_PAGE
    chunk = cat.products[start : start + config.PRICE_PER_PAGE]
    lines = [f"📁 {cat.path}", ""]
    for p in chunk:
        price = price_for(user, p.price)
        lines.append(f"• {p.name}\n  {fmt_price(price)} ₽")
    await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())


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
        f"{product.name}\n\n"
        f"Артикул: {product.code}\n"
        f"Цена: {fmt_price(price)} ₽"
    )
    kb = keyboards.InlineKeyboardBuilder()
    kb.row(keyboards.InlineKeyboardButton(text="← Назад", callback_data="cmd:menu"))
    await call.message.edit_text(text, reply_markup=kb.as_markup())
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
    start = page * config.SEARCH_PER_PAGE
    chunk = results[start : start + config.SEARCH_PER_PAGE]
    lines = [f"Результаты по запросу «{query}»:", ""]
    for p in chunk:
        price = price_for(user, p.price)
        lines.append(f"• {p.name}\n  {fmt_price(price)} ₽")
    kb = keyboards.search_results(results, page, config.SEARCH_PER_PAGE, query)
    await call.message.edit_text("\n".join(lines), reply_markup=kb.as_markup())
    await call.answer()


# ---------------- Оптовые заявки (для админа) ----------------

@dp.callback_query(F.data.startswith("opt:"))
async def cb_opt(call: CallbackQuery):
    if call.from_user.id != config.ADMIN_ID:
        await call.answer("Нет доступа", show_alert=True)
        return
    _, action, user_id = call.data.split(":")
    user_id = int(user_id)
    user = db.get_user(user_id)
    if user is None:
        await call.answer("Пользователь не найден", show_alert=True)
        return
    if action == "approve":
        db.set_role(user_id, "wholesaler")
        await call.message.edit_text(
            f"✅ Пользователь {user['full_name'] or user_id} получил оптовый доступ.",
            reply_markup=keyboards.admin_panel(db.pending_requests()).as_markup() if db.pending_requests() else keyboards.admin_menu().as_markup(),
        )
        await safe_send(
            user_id,
            "Поздравляем! Ваш оптовый доступ подтверждён. Теперь цены отображаются по прайсу.",
        )
    elif action == "reject":
        db.set_role(user_id, "buyer")
        db.clear_opt_request(user_id)
        await call.message.edit_text(
            f"❌ Заявка пользователя {user['full_name'] or user_id} отклонена.",
            reply_markup=keyboards.admin_panel(db.pending_requests()).as_markup() if db.pending_requests() else keyboards.admin_menu().as_markup(),
        )
        await safe_send(user_id, "К сожалению, ваша заявка на оптовый доступ была отклонена.")
    await call.answer()


async def on_startup():
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
    dp.startup.register(on_startup)
    await asyncio.gather(_run_http_server(), dp.start_polling(bot))


if __name__ == "__main__":
    asyncio.run(main())