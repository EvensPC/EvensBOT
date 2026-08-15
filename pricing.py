import config


def price_for(user: dict | None, wholesale_price: int) -> int:
    if user and user.get("role") == "wholesaler":
        return wholesale_price
    return round(wholesale_price * config.RETAIL_MARKUP)


def fmt_price(value: int) -> str:
    return f"{value:,}".replace(",", " ")