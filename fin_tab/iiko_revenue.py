"""Self-contained iiko revenue fetcher for FinTablo tasks (bar, kitchen, app, yandex)."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List

import httpx
import pandas as pd

from fin_tab import iiko_auth

logger = logging.getLogger(__name__)

# Allowed filters reused from legacy logic
_BAR_ALLOWED_PAY = {"Наличные", "Оплата картой Сбербанк"}
_BAR_ALLOWED_CATEGORIES = {
    None,
    "Батончики",
    "Выпечка",
    "Горячие напитки",
    "Добавки",
    "Завтраки",
    "Закуски",
    "Кофе",
    "Лимонады",
    "Обучение ",
    "Персонал",
    "Пиво",
    "Пицца",
    "Пицца Яндекс",
    "Растительное молоко",
    "Реализация",
    "Салаты",
    "Свежевыжатые соки",
    "Соус",
    "Супы",
    "ТМЦ",
    "Холодные напитки",
    "ЯНДЕКС",
}

_KITCHEN_ALLOWED_PAY = {"Наличные", "Оплата картой Сбербанк"}
_KITCHEN_ALLOWED_CATEGORIES = {
    None,
    "Выпечка",
    "Горячие напитки",
    "Добавки",
    "Завтраки",
    "Закуски",
    "Кофе",
    "Лимонады",
    "Персонал",
    "Пиво",
    "Пицца",
    "Пицца Яндекс",
    "Растительное молоко",
    "Реализация",
    "Салаты",
    "Соус",
    "Супы",
    "Холодные напитки",
    "ЯНДЕКС",
}

_YANDEX_ALLOWED_CATEGORIES = _KITCHEN_ALLOWED_CATEGORIES | _BAR_ALLOWED_CATEGORIES

_APP_ALLOWED_PAY = {"Оплата в приложении (Loyalhub)", "Проведенная оплата (LoyalHub)"}

# Pay types we request from iiko to match historic filters
_REQUESTED_PAY_TYPES = [
    "Наличные",
    "Оплата в приложении (Loyalhub)",
    "Проведенная оплата (LoyalHub)",
    "Оплата картой при получении (Loyalhub)",
    "Оплата картой Сбербанк",
    "Яндекс.оплата",
]

# Dish categories filter to reduce payload
_REQUESTED_DISH_CATEGORIES = list(_BAR_ALLOWED_CATEGORIES - {None})

YANDEX_COMMISSION_PERCENT = 36.5


def _parse_xml_report(xml: str) -> List[Dict[str, Any]]:
    """Parse XML rows into list of dicts (minimal, for SALES report)."""
    import xml.etree.ElementTree as ET  # local import to avoid overhead if unused

    def _auto_cast(text: str | None):
        if text is None:
            return None
        try:
            return int(text)
        except Exception:
            try:
                return float(text)
            except Exception:
                return text.strip()

    root = ET.fromstring(xml)
    rows: List[Dict[str, Any]] = []
    for row in root.findall("./r"):
        rows.append({child.tag: _auto_cast(child.text) for child in row})
    return rows


async def get_revenue_report(date_from: str, date_to: str) -> List[Dict[str, Any]]:
    """Fetch iiko SALES OLAP for the given period (date strings in YYYY-MM-DD)."""
    token = await iiko_auth.get_auth_token()
    base_url = iiko_auth.get_base_url()

    date_from_display = datetime.strptime(date_from, "%Y-%m-%d").strftime("%d.%m.%Y")
    date_to_display = datetime.strptime(date_to, "%Y-%m-%d").strftime("%d.%m.%Y")

    params = [
        ("key", token),
        ("report", "SALES"),
        ("from", date_from_display),
        ("to", date_to_display),
        ("groupRow", "CookingPlaceType"),
        ("groupRow", "PayTypes"),
        ("groupRow", "DishCategory"),
        ("groupRow", "DishName"),
        ("groupRow", "DeletedWithWriteoff"),
        ("groupRow", "OrderDeleted"),
        ("agr", "DishSumInt"),
        ("agr", "DishDiscountSumInt"),
        ("agr", "ProductCostBase.ProductCost"),
        ("DeletedWithWriteoff", "NOT_DELETED"),
        ("OrderDeleted", "NOT_DELETED"),
    ]

    for payment in _REQUESTED_PAY_TYPES:
        params.append(("PayTypes", payment))

    for category in _REQUESTED_DISH_CATEGORIES:
        params.append(("DishCategory", category))

    logger.info("Запрос iiko SALES %s - %s", date_from_display, date_to_display)

    async with httpx.AsyncClient(base_url=base_url, timeout=60, verify=False) as client:
        resp = await client.get("/resto/api/reports/olap", params=params)

    resp.raise_for_status()
    ct = resp.headers.get("content-type", "")

    if ct.startswith("application/json"):
        data = resp.json()
        report_data = data.get("data", []) or data.get("rows", [])
    elif ct.startswith("application/xml") or ct.startswith("text/xml"):
        report_data = _parse_xml_report(resp.text)
    else:
        raise RuntimeError(f"Неизвестный формат ответа: {ct}")

    return report_data


def _prepare_df(data: List[Dict[str, Any]]) -> pd.DataFrame:
    df = pd.DataFrame(data)
    if df.empty:
        return df

    pay_types_col = "PayTypes.Combo" if "PayTypes.Combo" in df.columns else "PayTypes"
    cooking_place_col = "CookingPlace" if "CookingPlace" in df.columns else "CookingPlaceType"

    if pay_types_col not in df.columns:
        raise ValueError("В отчете отсутствует колонка оплаты")
    if cooking_place_col not in df.columns:
        raise ValueError("В отчете отсутствует колонка места приготовления")

    df = df.copy()
    df[pay_types_col] = df[pay_types_col].astype(str)
    df[cooking_place_col] = df[cooking_place_col].astype(str)

    no_payment_mask = df[pay_types_col].str.contains("без оплаты", case=False, na=False)
    if no_payment_mask.any():
        df = df[~no_payment_mask].copy()

    return df


def calculate_bar_metrics(data: List[Dict[str, Any]]) -> Dict[str, float]:
    """Compute bar revenue (and cost) using local filters only."""
    df = _prepare_df(data)
    if df.empty:
        return {"bar_revenue": 0.0, "bar_cost": 0.0}

    pay_types_col = "PayTypes.Combo" if "PayTypes.Combo" in df.columns else "PayTypes"
    cooking_place_col = "CookingPlace" if "CookingPlace" in df.columns else "CookingPlaceType"

    df_bar_source = df.copy()

    pay_series_bar = df_bar_source[pay_types_col]
    bar_pay_mask = pay_series_bar.isna() | pay_series_bar.astype(str).isin(_BAR_ALLOWED_PAY)

    if "DishCategory" in df_bar_source.columns:
        cat_series_bar = df_bar_source["DishCategory"]
        bar_category_mask = cat_series_bar.isna() | cat_series_bar.astype(str).isin(_BAR_ALLOWED_CATEGORIES)
    else:
        bar_category_mask = True

    bar_mask = (
        df_bar_source[cooking_place_col].str.lower() == "бар"
    ) & bar_pay_mask & bar_category_mask

    bar_revenue = df_bar_source[bar_mask]["DishDiscountSumInt"].sum() if "DishDiscountSumInt" in df_bar_source.columns else 0

    cost_col = "ProductCostBase.ProductCost"
    bar_cost = df_bar_source[bar_mask][cost_col].sum() if cost_col in df_bar_source.columns else 0

    return {
        "bar_revenue": float(bar_revenue),
        "bar_cost": float(bar_cost),
    }


def calculate_kitchen_metrics(data: List[Dict[str, Any]]) -> Dict[str, float]:
    df = _prepare_df(data)
    if df.empty:
        return {"kitchen_revenue": 0.0, "kitchen_cost": 0.0}

    pay_types_col = "PayTypes.Combo" if "PayTypes.Combo" in df.columns else "PayTypes"
    cooking_place_col = "CookingPlace" if "CookingPlace" in df.columns else "CookingPlaceType"

    pay_series = df[pay_types_col]
    kitchen_pay_mask = pay_series.isna() | pay_series.astype(str).isin(_KITCHEN_ALLOWED_PAY)

    if "DishCategory" in df.columns:
        cat_series = df["DishCategory"]
        kitchen_category_mask = cat_series.isna() | cat_series.astype(str).isin(_KITCHEN_ALLOWED_CATEGORIES)
    else:
        kitchen_category_mask = True

    kitchen_place_mask = df[cooking_place_col].str.lower().isin(["кухня", "кухня-пицца", "пицца"])
    kitchen_mask = kitchen_place_mask & kitchen_pay_mask & kitchen_category_mask

    kitchen_revenue = df[kitchen_mask]["DishDiscountSumInt"].sum() if "DishDiscountSumInt" in df.columns else 0
    cost_col = "ProductCostBase.ProductCost"
    kitchen_cost = df[kitchen_mask][cost_col].sum() if cost_col in df.columns else 0

    return {
        "kitchen_revenue": float(kitchen_revenue),
        "kitchen_cost": float(kitchen_cost),
    }


def calculate_app_metrics(data: List[Dict[str, Any]]) -> Dict[str, float]:
    df = _prepare_df(data)
    if df.empty:
        return {"app_revenue": 0.0, "app_cost": 0.0}

    pay_types_col = "PayTypes.Combo" if "PayTypes.Combo" in df.columns else "PayTypes"

    pay_series = df[pay_types_col]
    app_mask = pay_series.astype(str).isin(_APP_ALLOWED_PAY)

    if "DishCategory" in df.columns:
        cat_series = df["DishCategory"]
        app_category_mask = cat_series.isna() | cat_series.astype(str).isin(_KITCHEN_ALLOWED_CATEGORIES)
    else:
        app_category_mask = True

    app_mask = app_mask & app_category_mask

    app_revenue = df[app_mask]["DishDiscountSumInt"].sum() if "DishDiscountSumInt" in df.columns else 0
    cost_col = "ProductCostBase.ProductCost"
    app_cost = df[app_mask][cost_col].sum() if cost_col in df.columns else 0

    return {
        "app_revenue": float(app_revenue),
        "app_cost": float(app_cost),
    }


def calculate_yandex_metrics(data: List[Dict[str, Any]]) -> Dict[str, float]:
    df = _prepare_df(data)
    if df.empty:
        return {"yandex_raw": 0.0, "yandex_fee": 0.0, "yandex_net": 0.0, "yandex_cost": 0.0}

    pay_types_col = "PayTypes.Combo" if "PayTypes.Combo" in df.columns else "PayTypes"
    is_yandex = df[pay_types_col].astype(str).str.contains("Яндекс.оплата", case=False, na=False)

    # В отчёте/боте для доставки используют сумму без скидки как базу
    yandex_raw = df[is_yandex]["DishSumInt"].sum() if "DishSumInt" in df.columns else 0
    yandex_fee = yandex_raw * (YANDEX_COMMISSION_PERCENT / 100)
    yandex_net = yandex_raw - yandex_fee

    cost_col = "ProductCostBase.ProductCost"
    yandex_cost = df[is_yandex][cost_col].sum() if cost_col in df.columns else 0

    return {
        "yandex_raw": float(yandex_raw),
        "yandex_fee": float(yandex_fee),
        "yandex_net": float(yandex_net),
        "yandex_cost": float(yandex_cost),
    }


def calculate_all_metrics(data: List[Dict[str, Any]]) -> Dict[str, float]:
    """Aggregate all revenue slices used for FinTablo postings."""
    bar = calculate_bar_metrics(data)
    kitchen = calculate_kitchen_metrics(data)
    app = calculate_app_metrics(data)
    yandex = calculate_yandex_metrics(data)

    result = {}
    result.update(bar)
    result.update(kitchen)
    result.update(app)
    result.update(yandex)
    return result
