import config


def price_for(user: dict | None, wholesale_price: int) -> int:
    if user and user.get("role") == "wholesaler":
        return wholesale_price
    return round(wholesale_price * config.RETAIL_MARKUP)


def fmt_price(value: int) -> str:
    return f"{value:,}".replace(",", "\u00A0")


def price_label(user: dict | None, wholesale_price: int) -> str:
    """Цена для показа пользователю: «12 500 ₽» или «по запросу»."""
    if wholesale_price <= 0:
        return "по запросу"
    return f"{fmt_price(price_for(user, wholesale_price))}\u00A0₽"