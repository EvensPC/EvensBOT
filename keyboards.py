from aiogram.utils.keyboard import InlineKeyboardBuilder, InlineKeyboardButton


def _btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)


def main_menu(catalog) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for root in catalog.roots:
        kb.add(_btn(root.name, f"cat:{root.key}"))
    kb.adjust(1)
    kb.row(_btn("Поиск товара", "cmd:search"), _btn("Мой профиль", "cmd:profile"))
    kb.row(_btn("Стать оптовиком", "cmd:wholesale"))
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
    kb.row(_btn("← Назад", back_cb))
    return kb


def products_menu(cat, page: int = 0, per_page: int = 5) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    total = len(cat.products)
    start = page * per_page
    end = min(start + per_page, total)
    kb.row(_btn(f"{cat.name} ({total} шт.)", f"info:{cat.key}"))
    for product in cat.products[start:end]:
        kb.row(_btn(product.name[:80], f"prod:{product.code}"))
    nav = []
    if page > 0:
        nav.append(_btn("←", f"pg:{cat.key}:{page - 1}"))
    nav.append(_btn(f"{page + 1}/{max(1, (total + per_page - 1) // per_page)}", f"info:{cat.key}"))
    if end < total:
        nav.append(_btn("→", f"pg:{cat.key}:{page + 1}"))
    if nav:
        kb.row(*nav)
    if cat.parent is not None:
        back_cb = f"cat:{cat.parent.key}"
    else:
        back_cb = "cmd:menu"
    kb.row(_btn("← Назад", back_cb))
    return kb


def search_results(results, page: int = 0, per_page: int = 8, query: str = "") -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    total = len(results)
    start = page * per_page
    end = min(start + per_page, total)
    kb.row(_btn(f"Найдено: {total}", f"info:search:{query[:40]}"))
    for product in results[start:end]:
        kb.row(_btn(product.name[:80], f"prod:{product.code}"))
    nav = []
    if page > 0:
        nav.append(_btn("←", f"spg:{page - 1}:{query[:40]}"))
    nav.append(_btn(f"{page + 1}/{max(1, (total + per_page - 1) // per_page)}", f"info:search:{query[:40]}"))
    if end < total:
        nav.append(_btn("→", f"spg:{page + 1}:{query[:40]}"))
    if nav:
        kb.row(*nav)
    kb.row(_btn("← В меню", "cmd:menu"))
    return kb


def profile_menu() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("← В меню", "cmd:menu"))
    return kb


def wholesale_menu() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.row(_btn("Запросить оптовый доступ", "cmd:request_opt"))
    kb.row(_btn("← В меню", "cmd:menu"))
    return kb


def admin_panel(requests) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for req in requests:
        kb.row(
            _btn(f"{req['full_name'] or req['username'] or req['user_id']}", f"cmd:request_opt"),
            _btn("✅", f"opt:approve:{req['user_id']}"),
            _btn("❌", f"opt:reject:{req['user_id']}"),
        )
    kb.row(_btn("← В меню", "cmd:menu"))
    return kb
