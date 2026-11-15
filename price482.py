"""Прайс 482: загрузка и сопоставление позиций с нашим каталогом.

Артикулы в двух прайсах не связаны, поэтому сопоставление идёт по названию.
Чтобы не показать неверную разницу, совпадение принимается только если:
  * все слова названия из прайса 482 есть в названии нашего товара;
  * цвет (если указан в обоих) совпадает;
  * названия сопоставимы по длине (отсекает общие названия вроде «Переходник HDMI–VGA»).
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

import openpyxl
import requests

SPREADSHEET_ID = "1aI1SuXZ2PfiKL4d4neY0xYRuWSHybS0PD1z3rFOnnM8"
EXPORT_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=xlsx"

# Цвет в прайсах пишут по-разному: «WH», «White», «белый».
_COLOR_ALIASES = {
    "white": "white",
    "белый": "white",
    "белая": "white",
    "белое": "white",
    "wh": "white",
    "black": "black",
    "черный": "black",
    "чёрный": "black",
    "черная": "black",
    "чёрная": "black",
    "bk": "black",
}
_COLORS = frozenset(_COLOR_ALIASES.values())

_MIN_TOKENS = 3  # слишком короткие названия не сопоставляем
_MIN_LENGTH_RATIO = 0.45  # защита от общих названий


@dataclass
class Price482Item:
    code: str
    name: str
    price: int
    tokens: frozenset[str] = field(default_factory=frozenset)
    colors: frozenset[str] = field(default_factory=frozenset)
    compact: str = ""


def _clean(value) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _to_int(value) -> int:
    try:
        return int(float(str(value).replace(" ", "").replace(",", ".")))
    except (TypeError, ValueError):
        return 0


def _clean_code(value) -> str:
    code = _clean(value)
    if code.endswith(".0"):
        code = code[:-2]
    return code


def _tokens(name: str) -> list[str]:
    """Слова названия: «RTX 5060 Ti 8GB» -> rtx, 5060ti, 8gb."""
    text = str(name or "").lower().replace("ё", "е")
    raw = [t for t in re.split(r"[^0-9a-zа-я]+", text) if t]
    result: list[str] = []
    i = 0
    while i < len(raw):
        token = raw[i]
        # Склеиваем число с коротким суффиксом: «5060 Ti» и «5060Ti» — одно слово.
        if token.isdigit() and i + 1 < len(raw) and len(raw[i + 1]) <= 2 and raw[i + 1].isalpha():
            token += raw[i + 1]
            i += 1
        result.append(_COLOR_ALIASES.get(token, token))
        i += 1
    return result


def _compact(name: str) -> str:
    text = str(name or "").lower().replace("ё", "е")
    text = re.sub(r"\([^)]*\)", "", text)
    return re.sub(r"[^0-9a-zа-я]+", "", text)


class Price482:
    def __init__(self) -> None:
        self.items: list[Price482Item] = []

    def rebuild(self, workbook_bytes: bytes) -> None:
        wb = openpyxl.load_workbook(io.BytesIO(workbook_bytes), data_only=True)
        ws = wb["Прайс 482"] if "Прайс 482" in wb.sheetnames else wb.active

        items: list[Price482Item] = []
        for row in ws.iter_rows(min_row=1, max_row=ws.max_row, max_col=4):
            code = _clean_code(row[0].value)
            name = _clean(row[1].value)
            price = _to_int(row[3].value)
            if not name or not code or price <= 0:
                continue
            tokens = _tokens(name)
            token_set = frozenset(tokens)
            if len(token_set) < _MIN_TOKENS:
                continue
            items.append(
                Price482Item(
                    code=code,
                    name=name,
                    price=price,
                    tokens=token_set,
                    colors=frozenset(t for t in token_set if t in _COLORS),
                    compact=_compact(name),
                )
            )
        if not items:
            raise ValueError("в прайсе 482 не найдено ни одной позиции")
        self.items = items

    def match(self, name: str) -> Price482Item | None:
        """Ищет позицию прайса 482 для названия товара (или None)."""
        if not self.items:
            return None
        token_set = set(_tokens(name))
        if len(token_set) < _MIN_TOKENS:
            return None
        colors = frozenset(t for t in token_set if t in _COLORS)
        compact_len = len(_compact(name))
        if not compact_len:
            return None

        best: Price482Item | None = None
        best_key: tuple[int, int] = (-1, -1)
        for item in self.items:
            if not item.tokens <= token_set:
                continue
            if len(item.compact) < _MIN_LENGTH_RATIO * compact_len:
                continue
            if colors and item.colors and colors != item.colors:
                continue
            key = (1 if colors and item.colors == colors else 0, len(item.tokens))
            if key > best_key:
                best, best_key = item, key
        return best


def fetch_xlsx_bytes() -> bytes:
    response = requests.get(EXPORT_URL, timeout=60)
    response.raise_for_status()
    return response.content
