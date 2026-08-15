from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton

from pricing import fmt_price, price_for


def _btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)


def main_menu(catalog, is_admin: bool = False) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for root in catalog.roots:
        kb.add(_btn(root.name, f"cat:{root.key}"))
    kb.adjust(1)
    kb.row(_btn("🔍 Поиск товара", "cmd:search"), _btn("👤 Мой профиль", "cmd:profile"))
    kb.row(_btn("🤝 Стать оптовиком", "cmd:wholesale"))
    if is_admin:
        kb.row(_btn("⚙️ Админ-панель", "cmd:admin"))
    return kb


def category_menu(cat) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for child in cat.children:
        kb.add(_btn(child.name, f"cat:{child.key}"))
    kb.adjust(1)
    if cat.parent is not None:
        back_cb = f"cat:{cat.parent.key}"
    else:
        back_cb = "cmd:menu"
    kb.row(_btn("← Назад", back_cb), _btn("🏠 Меню", "cmd:menu"))
    return kb


def products_menu(cat, page: int = 0, per_page: int = 0, user: dict | None = None) -> InlineKeyboardBuilder:
    """Только навигация (список товаров показывается в тексте)."""
    kb = InlineKeyboardBuilder()
    total = len(cat.products)
    if per_page:
        start = page * per_page
        pages = max(1, (total + per_page - 1) // per_page)
        nav = []
        if page > 0:
            nav.append(_btn("◀", f"pg:{cat.key}:{page - 1}"))
        nav.append(_btn(f"{page + 1}/{pages}", f"info:{cat.key}"))
        if start + per_page < total:
            nav.append(_btn("▶", f"pg:{cat.key}:{page + 1}"))
        kb.row(*nav)
    if cat.parent is not None:
        back_cb = f"cat:{cat.parent.key}"
    else:
        back_cb = "cmd:menu"
    kb.row(_btn("← Назад", back_cb), _btn("🏠 Меню", "cmd:menu"))
    return kb


def search_results(results, page: int = 0, per_page: int = 10, query: str = "", user: dict | None = None) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    start = page * per_page
    end = min(start + per_page, len(results))
    for i, product in enumerate(results[start:end], start=start + 1):
        price = price_for(user, product.price)
        label = f"{i}. {product.name[:55]} — {fmt_price(price)} ₽"
        kb.row(_btn(label, f"prod:{product.code}"))
    nav = []
    if page > 0:
        nav.append(_btn("◀", f"spg:{page - 1}:{query[:40]}"))
    nav.append(_btn(f"{page + 1}/{max(1, (len(results) + per_page - 1) // per_page)}", f"info:search:{query[:40]}"))
    if end < len(results):
        nav.append(_btn("▶", f"spg:{page + 1}:{query[:40]}"))
    if nav:
        kb.row(*nav)
    kb.row(_btn("🏠 Меню", "cmd:menu"))
    return kb


def profile_menu() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🏠 Меню", "cmd:menu"))
    return kb


def wholesale_menu() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("📨 Запросить оптовый доступ", "cmd:request_opt"))
    kb.row(_btn("🏠 Меню", "cmd:menu"))
    return kb


def admin_menu() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("🔄 Обновить список", "cmd:admin"))
    kb.row(_btn("🧾 Оптовики", "admin:list"))
    kb.row(_btn("🏠 Меню", "cmd:menu"))
    return kb


def admin_panel(requests) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for req in requests:
        kb.row(
            _btn(f"{req['full_name'] or req['username'] or req['user_id']}", "cmd:admin"),
            _btn("✅", f"opt:approve:{req['user_id']}"),
            _btn("❌", f"opt:reject:{req['user_id']}"),
        )
    kb.row(_btn("🧾 Оптовики", "admin:list"))
    kb.row(_btn("🏠 Меню", "cmd:menu"))
    return kb


def wholesalers_menu() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("⬅️ В админ-панель", "cmd:admin"))
    kb.row(_btn("🏠 Меню", "cmd:menu"))
    return kb