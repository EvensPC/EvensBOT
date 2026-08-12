import io
import re
from dataclasses import dataclass, field

import openpyxl
import requests

from categories import ROOT_CATEGORIES, EXCLUDED_ROOTS

SPREADSHEET_ID = "1YloYvrGUqWY45khtZUVKbfmAupR1PotJhXUTTTP--JA"
EXPORT_URL = f"https://docs.google.com/spreadsheets/d/{SPREADSHEET_ID}/export?format=xlsx"


def _clean(value):
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


@dataclass
class Product:
    code: str
    name: str
    price: int
    link: str = ""


@dataclass
class Category:
    name: str
    key: str
    parent: "Category | None" = None
    children: list["Category"] = field(default_factory=list)
    products: list[Product] = field(default_factory=list)

    @property
    def path(self):
        parts = []
        node = self
        while node is not None:
            parts.append(node.name)
            node = node.parent
        return " / ".join(reversed(parts))


class Catalog:
    def __init__(self):
        self.roots: list[Category] = []
        self.all_products: list[Product] = []
        self._index: dict[str, Category] = {}

    def _register(self, cat: Category):
        self._index[cat.key] = cat
        self.all_products.extend(cat.products)
        for child in cat.children:
            self._register(child)

    def get(self, key: str) -> Category | None:
        return self._index.get(key)

    @staticmethod
    def _find_open_parent(stack):
        if not stack:
            return None
        deepest = stack[-1]
        if not deepest.products and not deepest.children:
            return deepest
        if deepest.parent is not None:
            return deepest.parent
        return deepest

    def rebuild(self, workbook_bytes: bytes):
        wb = openpyxl.load_workbook(io.BytesIO(workbook_bytes), data_only=True)
        ws = wb.active

        self.roots = []
        self.all_products = []
        self._index = {}

        stack: list[Category] = []
        current_root: Category | None = None
        node_counter = 0

        for row in ws.iter_rows(min_row=4, max_col=5):
            a, b, c, d, e = (cell.value for cell in row)

            bold = _is_bold(row[1])
            header_name = _clean(b)
            code = _clean(b)
            product_name = _clean(c)

            if not bold and not product_name:
                continue

            if bold and header_name:
                if header_name in ROOT_CATEGORIES:
                    node_counter += 1
                    cat = Category(name=header_name, key=f"c{node_counter}")
                    self.roots.append(cat)
                    stack = [cat]
                    current_root = cat
                else:
                    node_counter += 1
                    cat = Category(name=header_name, key=f"c{node_counter}")
                    parent = self._find_open_parent(stack)
                    if parent is None:
                        self.roots.append(cat)
                        stack = [cat]
                        current_root = cat
                    else:
                        cat.parent = parent
                        parent.children.append(cat)
                        stack = stack[: stack.index(parent) + 1] + [cat]
                continue

            if not product_name:
                continue

            try:
                price_int = int(float(d))
            except (TypeError, ValueError):
                price_int = 0

            product = Product(code=code, name=product_name, price=price_int, link=_clean(e))

            target = stack[-1] if stack else current_root
            if target is None:
                continue
            target.products.append(product)

        self.roots = [root for root in self.roots if root.name not in EXCLUDED_ROOTS]

        for root in self.roots:
            self._register(root)

    @property
    def updated(self) -> bool:
        return bool(self.roots)


def _is_bold(cell) -> bool:
    try:
        return bool(cell.font.bold)
    except Exception:
        return False


def fetch_xlsx_bytes() -> bytes:
    response = requests.get(EXPORT_URL, timeout=60)
    response.raise_for_status()
    return response.content