"""
Модуль для получения и обработки отчета "Выручка и себестоимость" из iiko
Использует специальный отчет ID: 3646ed72-6eee-4085-9179-4f7e88fa1cac
"""

import logging
import httpx
import pandas as pd
from typing import Dict, Any
from datetime import datetime, timedelta
from iiko.iiko_auth import get_auth_token, get_base_url
from db.settings_db import get_yandex_commission
from services.writeoff_documents import get_writeoff_documents, get_writeoff_cost_olap
from services.salary_from_iiko import fetch_salary_from_iiko
from db.departments_db import get_all_department_positions, DEPARTMENTS
from services.cost_plan import get_cost_plan_summary
import xml.etree.ElementTree as ET
from decimal import Decimal

logger = logging.getLogger(__name__)

BAR_COOKING_PLACES = {"бар"}
KITCHEN_COOKING_PLACES = {"кухня", "кухня-пицца", "пицца"}
YANDEX_PAYMENT_KEYWORD = "яндекс"
NO_PAYMENT_LABEL = "(без оплаты)"
CATEGORY_EXCLUDE_FOR_COST = {"Модификаторы", "Персонал", "Расходные материалы"}

REPORT_ID = "3646ed72-6eee-4085-9179-4f7e88fa1cac"  # Старый preset (не работает с датами)


def _auto_cast(text):
    """Автоматическое приведение типов из XML"""
    if text is None:
        return None
    try:
        return int(text)
    except Exception:
        try:
            return Decimal(text)
        except Exception:
            return text.strip()


def parse_xml_report(xml: str):
    """Парсинг XML отчета в список словарей"""
    root = ET.fromstring(xml)
    rows = []
    for row in root.findall("./r"):
        rows.append({child.tag: _auto_cast(child.text) for child in row})
    return rows


async def get_revenue_report_olap(date_from: str, date_to: str) -> list:
    """
    Получить отчет по выручке через обычный OLAP API (не preset)
    Этот метод правильно учитывает параметры дат!
    
    Args:
        date_from: дата начала в формате YYYY-MM-DD
        date_to: дата конца в формате YYYY-MM-DD
        
    Returns:
        список словарей с данными отчета
    """
    token = await get_auth_token()
    base_url = get_base_url()
    
    # ⚠️ ВАЖНО: Обычный OLAP API ожидает формат DD.MM.YYYY (не YYYY-MM-DD!)
    date_from_display = datetime.strptime(date_from, "%Y-%m-%d").strftime("%d.%m.%Y")
    date_to_display = datetime.strptime(date_to, "%Y-%m-%d").strftime("%d.%m.%Y")
    
    # Формируем параметры для OLAP запроса
    params = [
        ("key", token),
        ("report", "SALES"),
        ("from", date_from_display),
        ("to", date_to_display),
        ("groupRow", "CookingPlaceType"),    # Группировка по месту приготовления
        ("groupRow", "PayTypes"),             # Группировка по типу оплаты
        ("groupRow", "DishCategory"),         # Для фильтрации по категориям
        ("groupRow", "DishName"),             # Название блюда для детального анализа
        ("groupRow", "DeletedWithWriteoff"),  # Для фильтрации удалённых
        ("groupRow", "OrderDeleted"),         # Для фильтрации удалённых заказов
        ("agr", "DishSumInt"),                # Сумма без скидки
        ("agr", "DishDiscountSumInt"),        # Сумма со скидкой
        ("agr", "ProductCostBase.ProductCost"),  # Себестоимость
        ("DeletedWithWriteoff", "NOT_DELETED"),
        ("OrderDeleted", "NOT_DELETED"),
    ]
    
    # Добавляем фильтр по типам оплаты
    payment_types = [
        "Наличные",
        "Оплата в приложении (Loyalhub)",
        "Оплата картой при получении (Loyalhub)",
        "Оплата картой Сбербанк",
        "Яндекс.оплата"
    ]
    for payment in payment_types:
        params.append(("PayTypes", payment))
    
    # Добавляем фильтр по категориям блюд (из preset отчета)
    dish_categories = [
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
        "ЯНДЕКС"
    ]
    for category in dish_categories:
        params.append(("DishCategory", category))
    
    logger.info(f"🆕 Запрос OLAP отчета SALES, период: {date_from_display} - {date_to_display}")
    
    async with httpx.AsyncClient(base_url=base_url, timeout=60, verify=False) as client:
        url = "/resto/api/reports/olap"
        r = await client.get(url, params=params)
        
        logger.info(f"Статус ответа: {r.status_code}")
        ct = r.headers.get("content-type", "")
        logger.info(f"Content-Type: {ct}")
        
        if r.status_code != 200:
            logger.error(f"Ошибка получения отчета: {r.status_code}")
            logger.error(f"Ответ: {r.text[:500]}")
            raise RuntimeError(f"Ошибка получения отчета: HTTP {r.status_code}")
        
        # Парсим ответ (может быть XML или JSON)
        if ct.startswith("application/json"):
            data = r.json()
            report_data = data.get("data", []) or data.get("rows", [])
        elif ct.startswith("application/xml") or ct.startswith("text/xml"):
            report_data = parse_xml_report(r.text)
        else:
            logger.error(f"Неизвестный Content-Type: {ct}")
            raise RuntimeError(f"Неизвестный формат ответа: {ct}")
        
        logger.info(f"Получено {len(report_data)} строк из OLAP отчета")
        
        return report_data


async def get_revenue_report(date_from: str, date_to: str) -> list:
    """
    Получить отчет по выручке и себестоимости из iiko через OLAP API
    
    Args:
        date_from: дата начала в формате YYYY-MM-DD
        date_to: дата конца в формате YYYY-MM-DD
        
    Returns:
        список словарей с данными отчета
        
    Структура отчета:
        - CookingPlaceType: Тип места приготовления (Бар, Кухня, Пицца и т.д.)
        - PayTypes: Тип оплаты (Наличные, Яндекс.оплата и т.д.)
        - DishSumInt: Сумма без скидки
        - DishDiscountSumInt: Сумма со скидкой
    """
    logger.info("📊 Используем обычный OLAP API")
    return await get_revenue_report_olap(date_from, date_to)


async def calculate_revenue(data: list, date_from: str, date_to: str) -> Dict[str, Any]:
    """
    Рассчитать выручку по типам на основе данных отчета
    
    Args:
        data: список словарей с данными отчета
        date_from: дата начала в формате YYYY-MM-DD
        date_to: дата конца в формате YYYY-MM-DD
        
    Returns:
        словарь с рассчитанными значениями:
        {
            'bar_revenue': float,      # Выручка бара (без Яндекс)
            'kitchen_revenue': float,  # Выручка кухни (без Яндекс)
            'delivery_revenue': float, # Выручка доставки (Яндекс - комиссия)
            'yandex_commission': float,# Комиссия Яндекса (%)
            'yandex_raw': float,       # Выручка Яндекс до вычета комиссии
            'yandex_fee': float,       # Сумма комиссии Яндекса
            'writeoff_sum': float,     # Сумма расходных накладных
            'writeoff_count': int,     # Количество расходных накладных
            'days_without_writeoff': int  # Количество дней без расходных накладных
        }
    """
    if not data:
        logger.warning("Получены пустые данные отчета")
        return {
            'bar_revenue': 0.0,
            'kitchen_revenue': 0.0,
            'delivery_revenue': 0.0,
            'yandex_commission': 0.0,
            'yandex_raw': 0.0,
            'yandex_fee': 0.0,
            'writeoff_sum': 0.0,
            'writeoff_count': 0,
            'days_without_writeoff': 0
        }
    
    df = pd.DataFrame(data)
    logger.info(f"Получено {len(df)} строк отчета")
    
    # Приводим к числовым типам
    for col in ["DishSumInt", "DishDiscountSumInt", "ProductCostBase.ProductCost"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    
    # ⚠️ ВАЖНО: Фильтруем по DeletedWithWriteoff (блюдо не удалено)
    if "DeletedWithWriteoff" in df.columns:
        before = len(df)
        df = df[df["DeletedWithWriteoff"] == "NOT_DELETED"].copy()
        logger.info(f"Отфильтровано удаленных блюд: было {before}, осталось {len(df)}")
    
    if "OrderDeleted" in df.columns:
        df = df[df["OrderDeleted"] == "NOT_DELETED"].copy()
    
    # ⚠️ ВАЖНО: Фильтруем по DishCategory (только разрешённые категории)
    # OLAP API игнорирует параметр DishCategory, поэтому фильтруем в коде
    # Исключаем: Модификаторы, Расходные материалы (как в iiko)
    excluded_categories = list(CATEGORY_EXCLUDE_FOR_COST)
    if "DishCategory" in df.columns:
        before = len(df)
        df = df[~df["DishCategory"].isin(excluded_categories)].copy()
        logger.info(f"Отфильтровано по категориям блюд: было {before}, осталось {len(df)}")
    
    # Получаем процент комиссии Яндекса
    yandex_commission_percent = await get_yandex_commission()
    logger.info(f"Используем комиссию Яндекса: {yandex_commission_percent}%")
    
    # Фильтры - проверяем наличие колонок
    # В стандартном SALES отчете поля называются PayTypes.Combo и CookingPlace
    pay_types_col = "PayTypes.Combo" if "PayTypes.Combo" in df.columns else "PayTypes"
    cooking_place_col = "CookingPlace" if "CookingPlace" in df.columns else "CookingPlaceType"
    
    if pay_types_col not in df.columns:
        logger.error(f"Колонка '{pay_types_col}' не найдена в отчете!")
        logger.error(f"Доступные колонки: {df.columns.tolist()}")
        raise ValueError(f"В отчете отсутствует колонка оплаты")
    
    if cooking_place_col not in df.columns:
        logger.error(f"Колонка '{cooking_place_col}' не найдена в отчете!")
        logger.error(f"Доступные колонки: {df.columns.tolist()}")
        raise ValueError(f"В отчете отсутствует колонка места приготовления")
    
    # Исключаем строки "(без оплаты)" из расчета себестоимости (удаленные/отмененные блюда)
    no_payment_mask = df[pay_types_col].astype(str).str.contains("без оплаты", case=False, na=False)
    if no_payment_mask.any():
        df = df[~no_payment_mask].copy()
    is_yandex = df[pay_types_col].astype(str).str.contains("Яндекс.оплата", case=False, na=False)
    is_bar = df[cooking_place_col].astype(str).str.lower() == "бар"
    is_kitchen = df[cooking_place_col].astype(str).str.lower().isin(["кухня", "кухня-пицца", "пицца"])
    
    logger.debug(f"Строк с Яндекс.оплата: {is_yandex.sum()}")
    logger.debug(f"Строк с Бар: {is_bar.sum()}")
    logger.debug(f"Строк с Кухня: {is_kitchen.sum()}")
    
    # Детальное логирование Яндекс оплат для отладки
    if is_yandex.sum() > 0:
        yandex_details = df[is_yandex][[cooking_place_col, pay_types_col, "DishSumInt", "DishDiscountSumInt"]]
        
        for place in yandex_details[cooking_place_col].unique():
            place_data = yandex_details[yandex_details[cooking_place_col] == place]
            place_sum = place_data["DishSumInt"].sum()
            logger.debug(f"  Яндекс {place}: {place_sum:.2f}₽")
    
    # Выручка бара (со скидкой, без Яндекс)
    bar_revenue = df[is_bar & ~is_yandex]["DishDiscountSumInt"].sum()
    
    # Выручка кухни (со скидкой, без Яндекс)
    kitchen_revenue = df[is_kitchen & ~is_yandex]["DishDiscountSumInt"].sum()
    
    # Выручка Яндекс (БЕЗ скидки)
    yandex_raw = df[is_yandex]["DishSumInt"].sum()
    
    # Комиссия Яндекса
    yandex_fee = yandex_raw * (yandex_commission_percent / 100)
    
    # Выручка доставки (после вычета комиссии)
    delivery_revenue = yandex_raw - yandex_fee
    
    logger.info(f"Выручка бара: {bar_revenue:.2f}₽")
    logger.info(f"Выручка кухни: {kitchen_revenue:.2f}₽")
    logger.info(f"Выручка Яндекс (до вычета): {yandex_raw:.2f}₽")
    logger.info(f"Комиссия Яндекса: {yandex_fee:.2f}₽")
    logger.info(f"Выручка доставки (после вычета): {delivery_revenue:.2f}₽")
    
    # ════════════════════════════════════════════════════════════════════
    # РАСЧЕТ СЕБЕСТОИМОСТИ И ПРОЦЕНТОВ
    # ════════════════════════════════════════════════════════════════════
    cost_col = "ProductCostBase.ProductCost"
    
    # 1. Себестоимость бара (без Яндекса, БЕЗ "(без оплаты)")
    bar_cost = df[is_bar & ~is_yandex][cost_col].sum() if cost_col in df.columns else 0
    bar_cost_percent = (bar_cost / bar_revenue * 100) if bar_revenue > 0 else 0
    
    # 2. Себестоимость кухни (без Яндекса, БЕЗ "(без оплаты)")
    kitchen_cost = df[is_kitchen & ~is_yandex][cost_col].sum() if cost_col in df.columns else 0
    kitchen_cost_percent = (kitchen_cost / kitchen_revenue * 100) if kitchen_revenue > 0 else 0
    
    # 3. Себестоимость Яндекса (только Яндекс)
    yandex_cost = df[is_yandex][cost_col].sum() if cost_col in df.columns else 0
    yandex_cost_percent = (yandex_cost / delivery_revenue * 100) if delivery_revenue > 0 else 0
    
    # 4. Общая себестоимость кухни (включая Яндекс, БЕЗ "(без оплаты)")
    kitchen_total_cost = df[is_kitchen][cost_col].sum() if cost_col in df.columns else 0
    kitchen_delivery_revenue = kitchen_revenue + delivery_revenue
    kitchen_total_cost_percent = (kitchen_total_cost / kitchen_delivery_revenue * 100) if kitchen_delivery_revenue > 0 else 0
    
    logger.info(f"Себестоимость бара: {bar_cost:.2f}₽ ({bar_cost_percent:.1f}%)")
    logger.info(f"Себестоимость кухни: {kitchen_cost:.2f}₽ ({kitchen_cost_percent:.1f}%)")
    logger.info(
        "Себестоимость Яндекс: %.2f₽ (%.1f%% от выручки доставки после комиссии)",
        yandex_cost,
        yandex_cost_percent,
    )
    logger.info(f"Себестоимость кухни общая: {kitchen_total_cost:.2f}₽ ({kitchen_total_cost_percent:.1f}%)")
    
    # 5. Общая себестоимость (все категории)
    total_cost = bar_cost + kitchen_total_cost
    total_revenue = bar_revenue + kitchen_revenue + delivery_revenue
    total_cost_percent = (total_cost / total_revenue * 100) if total_revenue > 0 else 0
    
    logger.info(f"Общая себестоимость: {total_cost:.2f}₽ ({total_cost_percent:.1f}%)")
    
    # 6. Расходные накладные
    writeoff_data = await calculate_writeoffs(date_from, date_to)
    
    return {
        'bar_revenue': float(bar_revenue),
        'kitchen_revenue': float(kitchen_revenue),
        'delivery_revenue': float(delivery_revenue),
        'yandex_commission': float(yandex_commission_percent),
        'yandex_raw': float(yandex_raw),
        'yandex_fee': float(yandex_fee),
        # Себестоимость
        'bar_cost': float(bar_cost),
        'bar_cost_percent': float(bar_cost_percent),
        'kitchen_cost': float(kitchen_cost),
        'kitchen_cost_percent': float(kitchen_cost_percent),
        'yandex_cost': float(yandex_cost),
        'yandex_cost_percent': float(yandex_cost_percent),
        'kitchen_total_cost': float(kitchen_total_cost),
        'kitchen_total_cost_percent': float(kitchen_total_cost_percent),
        # Общая себестоимость
        'total_cost': float(total_cost),
        'total_cost_percent': float(total_cost_percent),
        # Расходные накладные
        'writeoff_revenue': writeoff_data['writeoff_revenue'],
        'writeoff_cost': writeoff_data.get('writeoff_cost', 0.0),
        'writeoff_cost_percent': writeoff_data.get('writeoff_cost_percent', 0.0),
        'writeoff_count': writeoff_data['writeoff_count'],
        'days_without_writeoff': writeoff_data['days_without_writeoff'],
        'total_days': writeoff_data['total_days'],
    }


# ════════════════════════════════════════════════════════════════════════════
# Новый отчёт: себестоимость по местам приготовления
# ════════════════════════════════════════════════════════════════════════════
async def analyze_cost_by_cooking_place(date_from: str, date_to: str) -> Dict[str, Any]:
    """Рассчитать себестоимость бара, кухни и Яндекса за период"""

    def _empty_result() -> Dict[str, Any]:
        return {
            'period_start': date_from,
            'period_end': date_to,
            'rows_total': 0,
            'rows_filtered': 0,
            'bar': {'revenue': 0.0, 'cost': 0.0, 'cost_percent': 0.0},
            'kitchen': {'revenue': 0.0, 'cost': 0.0, 'cost_percent': 0.0},
            'yandex': {
                'gross_revenue': 0.0,
                'net_revenue': 0.0,
                'commission_percent': 0.0,
                'commission_value': 0.0,
                'cost': 0.0,
                'cost_percent': 0.0,
            },
            'totals': {'revenue': 0.0, 'cost': 0.0, 'cost_percent': 0.0},
        }

    result = _empty_result()
    raw_data = await get_revenue_report(date_from, date_to)
    if not raw_data:
        logger.warning("Себестоимость по местам приготовления: отчёт пустой")
        return result

    df = pd.DataFrame(raw_data)
    result['rows_total'] = len(df)
    cost_col = "ProductCostBase.ProductCost"
    discount_col = "DishDiscountSumInt"
    sum_col = "DishSumInt"

    for column in (cost_col, discount_col, sum_col):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors='coerce').fillna(0)
        else:
            df[column] = 0.0

    pay_types_col = "PayTypes.Combo" if "PayTypes.Combo" in df.columns else "PayTypes"
    cooking_place_col = "CookingPlace" if "CookingPlace" in df.columns else "CookingPlaceType"

    missing_columns = [col for col in (pay_types_col, cooking_place_col) if col not in df.columns]
    if missing_columns:
        raise ValueError(f"В отчете отсутствуют обязательные колонки: {missing_columns}")

    df = df.copy()
    df[pay_types_col] = df[pay_types_col].astype(str).str.strip()
    df[cooking_place_col] = df[cooking_place_col].astype(str).str.strip()

    dish_col = None
    for candidate in ("DishName", "Dish"):
        if candidate in df.columns:
            dish_col = candidate
            break

    if "DeletedWithWriteoff" in df.columns:
        df = df[df["DeletedWithWriteoff"] == "NOT_DELETED"].copy()
    if "OrderDeleted" in df.columns:
        df = df[df["OrderDeleted"] == "NOT_DELETED"].copy()

    if "DishCategory" in df.columns:
        df = df[~df["DishCategory"].isin(CATEGORY_EXCLUDE_FOR_COST)].copy()

    no_payment_mask = df[pay_types_col].str.lower() == NO_PAYMENT_LABEL.lower()
    df = df[~no_payment_mask].copy()

    result['rows_filtered'] = len(df)
    if df.empty:
        logger.warning("Себестоимость по местам приготовления: после фильтров строк нет")
        return result

    yandex_mask = df[pay_types_col].str.contains(YANDEX_PAYMENT_KEYWORD, case=False, na=False)
    place_series = df[cooking_place_col].str.lower()
    bar_mask = place_series.isin(BAR_COOKING_PLACES) & ~yandex_mask
    kitchen_mask = place_series.isin(KITCHEN_COOKING_PLACES) & ~yandex_mask
    delivery_mask = yandex_mask

    df["RevenueWithDiscount"] = df[discount_col]
    yandex_commission_percent = await get_yandex_commission()
    commission_rate = yandex_commission_percent / 100 if yandex_commission_percent else 0.0
    df["NetYandexRevenue"] = df[sum_col] * (1 - commission_rate)

    def _calc_group(mask, revenue_col):
        revenue = float(df.loc[mask, revenue_col].sum())
        cost = float(df.loc[mask, cost_col].sum())
        percent = float((cost / revenue * 100) if revenue else 0.0)
        return revenue, cost, percent

    bar_revenue, bar_cost, bar_percent = _calc_group(bar_mask, "RevenueWithDiscount")
    kitchen_revenue, kitchen_cost, kitchen_percent = _calc_group(kitchen_mask, "RevenueWithDiscount")

    yandex_gross = float(df.loc[delivery_mask, sum_col].sum())
    yandex_net = float(df.loc[delivery_mask, "NetYandexRevenue"].sum())
    yandex_commission_value = yandex_gross - yandex_net
    yandex_cost = float(df.loc[delivery_mask, cost_col].sum())
    yandex_cost_percent = float((yandex_cost / yandex_net * 100) if yandex_net else 0.0)

    total_revenue = bar_revenue + kitchen_revenue + yandex_net
    total_cost = bar_cost + kitchen_cost + yandex_cost
    total_percent = float((total_cost / total_revenue * 100) if total_revenue else 0.0)
    kitchen_with_delivery_revenue = kitchen_revenue + yandex_net
    kitchen_with_delivery_cost = kitchen_cost + yandex_cost
    kitchen_with_delivery_percent = (
        float((kitchen_with_delivery_cost / kitchen_with_delivery_revenue * 100))
        if kitchen_with_delivery_revenue
        else 0.0
    )

    def _build_dish_stats(segment_mask, revenue_column):
        if not dish_col:
            return {'full': [], 'top_positive': [], 'top_negative': []}

        mask = segment_mask & df[dish_col].notna()
        if not mask.any():
            return {'full': [], 'top_positive': [], 'top_negative': []}

        available_cols = [dish_col, revenue_column, cost_col]
        aggregated = (
            df.loc[mask, available_cols]
            .groupby(dish_col, as_index=False)
            .sum()
            .rename(columns={
                dish_col: 'name',
                revenue_column: 'revenue',
                cost_col: 'cost',
            })
        )
        aggregated['margin'] = aggregated['revenue'] - aggregated['cost']
        aggregated['cost_percent'] = aggregated.apply(
            lambda row: (row['cost'] / row['revenue'] * 100) if row['revenue'] else 0.0,
            axis=1,
        )
        total_segment_cost = aggregated['cost'].sum()
        aggregated['cost_share_percent'] = (
            aggregated['cost'] / total_segment_cost * 100 if total_segment_cost else 0.0
        )

        def _to_python_records(frame):
            return [
                {
                    'name': str(row['name']).strip(),
                    'revenue': float(row['revenue']),
                    'cost': float(row['cost']),
                    'margin': float(row['margin']),
                    'cost_percent': float(row['cost_percent']),
                    'cost_share_percent': float(row['cost_share_percent']),
                }
                for _, row in frame.iterrows()
            ]

        full_records = _to_python_records(aggregated.sort_values(by='cost', ascending=False))

        positives = [record for record in full_records if record['margin'] > 0]
        negatives = [
            record
            for record in full_records
            if record['cost_percent'] >= 35.0
        ]

        def _negative_score(record: Dict[str, Any]) -> float:
            # Комбинированный показатель "плохо": большая себестоимость и высокий процент
            return record['cost'] * record['cost_percent']

        return {
            'full': full_records,
            'top_positive': sorted(positives, key=lambda x: x['margin'], reverse=True)[:5],
            'top_negative': sorted(negatives, key=_negative_score, reverse=True)[:5],
        }

    dishes_payload = {}
    if dish_col:
        dishes_payload['bar'] = _build_dish_stats(bar_mask, "RevenueWithDiscount")
        dishes_payload['kitchen'] = _build_dish_stats(kitchen_mask, "RevenueWithDiscount")
        dishes_payload['delivery'] = _build_dish_stats(delivery_mask, "NetYandexRevenue")

    result['bar'] = {'revenue': bar_revenue, 'cost': bar_cost, 'cost_percent': bar_percent}
    result['kitchen'] = {'revenue': kitchen_revenue, 'cost': kitchen_cost, 'cost_percent': kitchen_percent}
    result['yandex'] = {
        'gross_revenue': yandex_gross,
        'net_revenue': yandex_net,
        'commission_percent': yandex_commission_percent,
        'commission_value': yandex_commission_value,
        'cost': yandex_cost,
        'cost_percent': yandex_cost_percent,
    }
    result['totals'] = {
        'revenue': total_revenue,
        'cost': total_cost,
        'cost_percent': total_percent,
    }
    if dishes_payload:
        result['dishes'] = dishes_payload

    plan_comparison = {}
    plan_summary = None
    try:
        plan_summary = await get_cost_plan_summary(date_from, date_to)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось получить планы по себестоимости: %s", exc)

    if plan_summary:
        aggregated_plan = plan_summary.get('aggregated') or {}

        def _build_plan_entry(plan_value: float, fact_value: float) -> Dict[str, float]:
            delta = fact_value - plan_value
            delta_percent = (delta / plan_value * 100) if plan_value else None
            return {
                'plan': float(plan_value),
                'fact': float(fact_value),
                'delta': float(delta),
                'delta_percent': float(delta_percent) if delta_percent is not None else None,
            }

        bar_plan_value = aggregated_plan.get('bar')
        if bar_plan_value is not None:
            plan_comparison['bar'] = _build_plan_entry(bar_plan_value, bar_percent)

        kitchen_plan_value = aggregated_plan.get('kitchen')
        if kitchen_plan_value is not None:
            plan_comparison['kitchen_with_delivery'] = _build_plan_entry(
                kitchen_plan_value,
                kitchen_with_delivery_percent,
            )

        if plan_comparison:
            result['plan_comparison'] = plan_comparison
            result['plan_months'] = plan_summary.get('monthly', [])

    logger.info(
        "Себестоимость (бар/кухня/Яндекс): бар %.0f₽/%.0f₽, кухня %.0f₽/%.0f₽, Яндекс чистая %.0f₽",
        bar_revenue,
        bar_cost,
        kitchen_revenue,
        kitchen_cost,
        yandex_net,
    )

    return result


def format_cost_by_cooking_place_report(result: Dict[str, Any]) -> str:
    """Сформировать текстовый отчёт для Телеграма"""

    def _fmt_currency(value: float) -> str:
        return f"{value:,.2f} ₽".replace(",", " ")

    def _fmt_percent(value: float) -> str:
        return f"{value:.2f}%"

    def _fmt_date(date_str: str) -> str:
        try:
            return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
        except Exception:
            return date_str or "?"

    def _escape_md(text: str) -> str:
        """Экранирует спецсимволы Telegram Markdown в произвольных строках."""
        if text is None:
            return ""
        return str(text).replace("_", "\\_")

    bar = result.get('bar', {})
    kitchen = result.get('kitchen', {})
    yandex = result.get('yandex', {})
    totals = result.get('totals', {})
    plan_comparison = result.get('plan_comparison')
    dept_salaries = result.get('dept_salaries')

    lines = [
        "📑 *Себестоимость по категориям*",
        f"Период: {_fmt_date(result.get('period_start'))} — {_fmt_date(result.get('period_end'))}",
        "",
        "*Бар*",
        f"• Выручка: {_fmt_currency(bar.get('revenue', 0.0))}",
        f"• Себестоимость: {_fmt_currency(bar.get('cost', 0.0))} ({_fmt_percent(bar.get('cost_percent', 0.0))})",
        "",
        "*Кухня (вкл. пиццу)*",
        f"• Выручка: {_fmt_currency(kitchen.get('revenue', 0.0))}",
        f"• Себестоимость: {_fmt_currency(kitchen.get('cost', 0.0))} ({_fmt_percent(kitchen.get('cost_percent', 0.0))})",
        "",
        "*Яндекс*",
        f"• Выручка (грязная): {_fmt_currency(yandex.get('gross_revenue', 0.0))}",
        f"• Комиссия ({yandex.get('commission_percent', 0.0):.2f}%): {_fmt_currency(yandex.get('commission_value', 0.0))}",
        f"• Выручка (чистая): {_fmt_currency(yandex.get('net_revenue', 0.0))}",
        f"• Себестоимость: {_fmt_currency(yandex.get('cost', 0.0))} ({_fmt_percent(yandex.get('cost_percent', 0.0))})",
        "",
        "*Итого*",
        f"• Выручка: {_fmt_currency(totals.get('revenue', 0.0))}",
        f"• Себестоимость: {_fmt_currency(totals.get('cost', 0.0))} ({_fmt_percent(totals.get('cost_percent', 0.0))})",
    ]

    if plan_comparison:
        def _fmt_signed_percent(value: float) -> str:
            sign = "+" if value > 0 else ""
            return f"{sign}{value:.2f}%"

        lines.append("")
        lines.append("*План по проценту себестоимости*")
        for key, label in (
            ('bar', 'Бар'),
            ('kitchen_with_delivery', 'Кухня + доставка'),
        ):
            entry = plan_comparison.get(key)
            if not entry:
                continue
            emoji = "🔴" if entry['fact'] > entry['plan'] else "🟢"
            line = (
                f"{emoji} {label}: план {_fmt_percent(entry['plan'])}, "
                f"факт {_fmt_percent(entry['fact'])}, "
                f"Δ {_fmt_signed_percent(entry['delta'])} п.п."
            )
            delta_percent = entry.get('delta_percent')
            if delta_percent is not None:
                line += f" ({_fmt_signed_percent(delta_percent)} от плана)"
            lines.append(line)

    if isinstance(dept_salaries, dict):
        def _append_salary_line(label: str, value: float | None) -> float:
            if value is None:
                return 0.0
            lines.append(f"• {label}: {_fmt_currency(value)}")
            return float(value)

        lines.append("")
        lines.append("*ФОТ по цехам*")
        total_salary = 0.0
        for dept in DEPARTMENTS:
            total_salary += _append_salary_line(dept, dept_salaries.get(dept))

        other_keys = [key for key in dept_salaries.keys() if key not in (*DEPARTMENTS, 'Не распределено')]
        total_salary += _append_salary_line('Не распределено', dept_salaries.get('Не распределено'))
        for extra in sorted(other_keys):
            total_salary += _append_salary_line(extra, dept_salaries.get(extra))

        if total_salary > 0:
            lines.append(f"• Итого ФОТ: {_fmt_currency(total_salary)}")

    dishes = result.get('dishes') or {}
    if dishes:
        def _append_top_block(segment_key: str, title: str):
            segment = dishes.get(segment_key)
            if not segment:
                return
            lines.append("")
            lines.append(f"*{title}: ТОП блюд*")
            for heading, key, emoji in (
                ("Лучшие (маржа +)", 'top_positive', "✅"),
                ("Худшие (маржа -)", 'top_negative', "⚠️"),
            ):
                entries = segment.get(key) or []
                if not entries:
                    lines.append(f"{emoji} {heading}: нет данных")
                    continue
                lines.append(f"{emoji} {heading}:")
                for item in entries:
                    lines.append(
                        "• {name}: себестоимость {percent} ({share:.1f}% доля)".format(
                            name=_escape_md(item['name']),
                            percent=_fmt_percent(item.get('cost_percent', 0.0)),
                            share=item.get('cost_share_percent', 0.0),
                        )
                    )

        _append_top_block('bar', 'Бар')
        _append_top_block('kitchen', 'Кухня (вкл. пиццу)')
        _append_top_block('delivery', 'Доставка (Яндекс)')

    return "\n".join(lines)


def format_dishes_table(records: list[Dict[str, Any]], limit: int | None = None) -> str:
    """Вернуть табличное представление списка блюд"""

    if not records:
        return "нет данных"

    header = f"{'Блюдо':<32} | {'Себестоим.':>12} | {'Выручка':>12} | {'Маржа':>12} | {'Доля%':>7} | {'% себ.':>7}"
    lines = [header, "-" * len(header)]

    def _format_number(value: float) -> str:
        return f"{value:,.2f}".replace(",", " ")

    for idx, item in enumerate(records):
        if limit is not None and idx >= limit:
            break
        cost = _format_number(item['cost'])
        revenue = _format_number(item['revenue'])
        margin = _format_number(item['margin'])
        lines.append(
            f"{item['name']:<32} | {cost:>12} | {revenue:>12} | {margin:>12} | "
            f"{item.get('cost_share_percent', 0.0):>6.1f} | {item.get('cost_percent', 0.0):>6.1f}"
        )

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════════════════
# ОТКЛЮЧЕНО: Расчет расходных накладных (вынесен в отдельную кнопку)
# ════════════════════════════════════════════════════════════════════════════
async def calculate_writeoffs(date_from: str, date_to: str) -> Dict[str, Any]:
    """
    Получить данные по расходным накладным за период
    
    Выручка берется из API документов (outgoingInvoice)
    Себестоимость берется из OLAP отчета по проводкам (TRANSACTIONS)
    
    Args:
        date_from: дата начала в формате YYYY-MM-DD
        date_to: дата конца в формате YYYY-MM-DD
        
    Returns:
        словарь с данными по расходным накладным
    """
    try:
        # 1. Получаем выручку из API документов
        writeoff_docs = await get_writeoff_documents(date_from, date_to)
        writeoff_revenue = sum(doc['sum'] for doc in writeoff_docs)  # Выручка
        writeoff_count = len(writeoff_docs)
        
        # 2. Получаем себестоимость через OLAP TRANSACTIONS
        writeoff_cost = await get_writeoff_cost_olap(date_from, date_to)
        
        # 3. Рассчитываем процент себестоимости
        writeoff_cost_percent = (writeoff_cost / writeoff_revenue * 100) if writeoff_revenue > 0 else 0
        
        # 4. Считаем дни без расходных накладных
        writeoff_dates = set(doc['date'].date() for doc in writeoff_docs)
        from_dt = datetime.strptime(date_from, "%Y-%m-%d")
        to_dt = datetime.strptime(date_to, "%Y-%m-%d")
        total_days = (to_dt - from_dt).days + 1
        days_with_writeoff = len(writeoff_dates)
        days_without_writeoff = total_days - days_with_writeoff
        
        logger.info(f"Расходные накладные: выручка {writeoff_revenue:.2f}₽, себестоимость {writeoff_cost:.2f}₽ ({writeoff_cost_percent:.1f}%)")
        logger.info(f"Количество: {writeoff_count} шт., дней без накладных: {days_without_writeoff} из {total_days}")
        
        return {
            'writeoff_revenue': float(writeoff_revenue),
            'writeoff_cost': float(writeoff_cost),
            'writeoff_cost_percent': float(writeoff_cost_percent),
            'writeoff_count': writeoff_count,
            'days_without_writeoff': days_without_writeoff,
            'total_days': total_days
        }
    except Exception as e:
        logger.error(f"Ошибка получения расходных накладных: {e}")
        return {
            'writeoff_revenue': 0.0,
            'writeoff_cost': 0.0,
            'writeoff_cost_percent': 0.0,
            'writeoff_count': 0,
            'days_without_writeoff': 0,
            'total_days': 0
        }


# ════════════════════════════════════════════════════════════════════════════
# ОТКЛЮЧЕНО: Расчет ФОТ по цехам (вынесен в отдельную кнопку)
# ════════════════════════════════════════════════════════════════════════════


async def calculate_salary_by_departments(date_from: str, date_to: str) -> Dict[str, float]:
    """
    Рассчитать ФОТ (зарплату) по цехам
    
    Args:
        date_from: дата начала в формате YYYY-MM-DD
        date_to: дата конца в формате YYYY-MM-DD
        
    Returns:
        словарь {название_цеха: сумма_зарплат}
    """
    try:
        # Даты уже в правильном формате YYYY-MM-DD для API
        # Конвертируем для отображения в логах
        from_dt = datetime.strptime(date_from, "%Y-%m-%d")
        to_dt = datetime.strptime(date_to, "%Y-%m-%d")
        date_from_display = from_dt.strftime("%d.%m.%Y")
        date_to_display = to_dt.strftime("%d.%m.%Y")
        
        logger.info(f"🔍 Начинаем расчет ФОТ по цехам за период {date_from_display} - {date_to_display}")
        
        # Получаем зарплаты всех сотрудников (date_from и date_to уже в формате YYYY-MM-DD)
        salary_data = await fetch_salary_from_iiko(date_from, date_to)
        logger.info(f"📊 Получены данные по зарплате для {len(salary_data)} сотрудников")
        
        # Получаем привязку должностей к цехам
        dept_positions = await get_all_department_positions()
        logger.info(f"🏭 Загружена привязка должностей к цехам:")
        for dept, positions in dept_positions.items():
            logger.info(f"  {dept}: {len(positions)} должностей - {', '.join(positions)}")
        
        # Создаем обратный маппинг: должность -> цех
        position_to_dept = {}
        for dept, positions in dept_positions.items():
            for pos in positions:
                position_to_dept[pos] = dept
        
        # Инициализируем суммы по цехам
        dept_salaries = {dept: 0.0 for dept in DEPARTMENTS}
        dept_salaries["Не распределено"] = 0.0  # Для сотрудников без цеха
        
        # Детальное логирование расчетов в консоль
        logger.info("=" * 80)
        logger.info("📋 ДЕТАЛЬНЫЙ РАСЧЕТ ФОТ ПО СОТРУДНИКАМ И ЦЕХАМ")
        logger.info("=" * 80)
        
        # Распределяем зарплаты по цехам
        for emp_id, emp_data in salary_data.items():
            emp_name = emp_data.get('name', 'Неизвестно')
            position = emp_data.get('position', 'Неизвестно')
            total_payment = emp_data.get('total_payment', 0.0)
            regular_payment = emp_data.get('regular_payment', 0.0)
            bonus = emp_data.get('bonus', 0.0)
            work_days = emp_data.get('work_days', 0)
            total_hours = emp_data.get('total_hours', 0.0)
            
            # Определяем цех сотрудника
            dept = position_to_dept.get(position, "Не распределено")
            
            # Добавляем зарплату к цеху
            dept_salaries[dept] += total_payment
            
            # Детальное логирование каждого сотрудника
            logger.info(f"\n👤 {emp_name}")
            logger.info(f"   Должность: {position}")
            logger.info(f"   Цех: {dept}")
            logger.info(f"   Отработано: {work_days} дн. ({total_hours:.1f} ч.)")
            logger.info(f"   Оклад: {regular_payment:,.2f} ₽")
            logger.info(f"   Бонус: {bonus:,.2f} ₽")
            logger.info(f"   ИТОГО: {total_payment:,.2f} ₽")
        
        logger.info("\n" + "=" * 80)
        logger.info("💰 ИТОГО ПО ЦЕХАМ:")
        logger.info("=" * 80)
        for dept in DEPARTMENTS:
            logger.info(f"   {dept}: {dept_salaries[dept]:,.2f} ₽")
        if dept_salaries["Не распределено"] > 0:
            logger.info(f"   ⚠️ Не распределено: {dept_salaries['Не распределено']:,.2f} ₽")
        
        total_fot = sum(dept_salaries.values())
        logger.info(f"\n   📊 ОБЩИЙ ФОТ: {total_fot:,.2f} ₽")
        logger.info("=" * 80 + "\n")
        
        return dept_salaries
        
    except Exception as e:
        logger.exception(f"❌ Ошибка расчета ФОТ по цехам: {e}")
        # Возвращаем нулевые значения при ошибке
        result = {dept: 0.0 for dept in DEPARTMENTS}
        result["Не распределено"] = 0.0
        return result


def format_revenue_report(
    revenue_data: Dict[str, Any],
    date_from: str,
    date_to: str,
    dept_salaries: Dict[str, float] | None = None,
) -> str:
    """Сформировать текст отчёта по выручке для Телеграма."""

    def _fmt_currency(value: float) -> str:
        return f"{value:,.2f} ₽".replace(",", " ")

    def _fmt_percent(value: float) -> str:
        return f"{value:.1f}%"

    date_from_display = datetime.strptime(date_from, "%Y-%m-%d").strftime("%d.%m.%Y")
    date_to_display = datetime.strptime(date_to, "%Y-%m-%d").strftime("%d.%m.%Y")

    total_revenue = (
        revenue_data['bar_revenue']
        + revenue_data['kitchen_revenue']
        + revenue_data['delivery_revenue']
    )

    logger.info("📊 Расчет ИТОГО:")
    logger.info("  Бар: %.2f₽", revenue_data['bar_revenue'])
    logger.info("  Кухня: %.2f₽", revenue_data['kitchen_revenue'])
    logger.info("  Доставка: %.2f₽", revenue_data['delivery_revenue'])
    logger.info("  ИТОГО: %.2f₽", total_revenue)

    lines = [
        "💰 *ОТЧЕТ ПО ВЫРУЧКЕ*",
        f"Период: {date_from_display} - {date_to_display}",
        "",
        "🍹 *БАР*",
        f"  Выручка: {_fmt_currency(revenue_data['bar_revenue'])}",
        f"  Себестоимость: {_fmt_currency(revenue_data['bar_cost'])} ({_fmt_percent(revenue_data['bar_cost_percent'])})",
        "",
        "🍕 *КУХНЯ* (Кухня + Пицца)",
        f"  Выручка: {_fmt_currency(revenue_data['kitchen_revenue'])}",
        f"  Себестоимость: {_fmt_currency(revenue_data['kitchen_cost'])} ({_fmt_percent(revenue_data['kitchen_cost_percent'])})",
        "",
        "🚗 *ДОСТАВКА* (Яндекс)",
        f"  Выручка до вычета: {_fmt_currency(revenue_data['yandex_raw'])}",
        f"  Комиссия ({revenue_data['yandex_commission']:.1f}%): -{_fmt_currency(revenue_data['yandex_fee'])}",
        f"  Выручка после вычета: {_fmt_currency(revenue_data['delivery_revenue'])}",
        f"  Себестоимость: {_fmt_currency(revenue_data['yandex_cost'])} ({_fmt_percent(revenue_data['yandex_cost_percent'])})",
        "",
        "📊 *КУХНЯ ОБЩАЯ* (с доставкой)",
        f"  Себестоимость: {_fmt_currency(revenue_data['kitchen_total_cost'])} ({_fmt_percent(revenue_data['kitchen_total_cost_percent'])})",
        "",
        "💵 *ИТОГО ВЫРУЧКА*",
        f"  Выручка: {_fmt_currency(total_revenue)}",
        f"  Себестоимость: {_fmt_currency(revenue_data['total_cost'])} ({_fmt_percent(revenue_data['total_cost_percent'])})",
        "",
        "📦 *РАСХОДНЫЕ НАКЛАДНЫЕ*",
        f"  Выручка: {_fmt_currency(revenue_data.get('writeoff_revenue', 0.0))}",
        f"  Себестоимость: {_fmt_currency(revenue_data.get('writeoff_cost', 0.0))} ({_fmt_percent(revenue_data.get('writeoff_cost_percent', 0.0))})",
        f"  Количество: {revenue_data.get('writeoff_count', 0)} шт.",
        f"  Дней без накладных: {revenue_data.get('days_without_writeoff', 0)} из {revenue_data.get('total_days', 0)}",
    ]

    if isinstance(dept_salaries, dict) and dept_salaries:
        lines.append("")
        lines.append("🏭 *ФОТ по цехам*")
        total_salary = 0.0

        def _append_salary(label: str, value: float | None) -> None:
            nonlocal total_salary
            if value is None:
                return
            total_salary += float(value)
            lines.append(f"  {label}: {_fmt_currency(value)}")

        for dept in DEPARTMENTS:
            _append_salary(dept, dept_salaries.get(dept))

        extra_keys = [key for key in dept_salaries.keys() if key not in (*DEPARTMENTS, 'Не распределено')]
        _append_salary('Не распределено', dept_salaries.get('Не распределено'))
        for extra in sorted(extra_keys):
            _append_salary(extra, dept_salaries.get(extra))

        if total_salary > 0:
            lines.append(f"  Итого ФОТ: {_fmt_currency(total_salary)}")

    return "\n".join(lines)
