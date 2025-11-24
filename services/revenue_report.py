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
from services.writeoff_documents import get_writeoff_documents
from services.salary_from_iiko import fetch_salary_from_iiko
from db.departments_db import get_all_department_positions, DEPARTMENTS
import xml.etree.ElementTree as ET
from decimal import Decimal

logger = logging.getLogger(__name__)

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
        ("report", "SALES"),  # Название отчета
        ("from", date_from_display),  # OLAP ожидает DD.MM.YYYY!
        ("to", date_to_display),      # OLAP ожидает DD.MM.YYYY!
        ("groupRow", "CookingPlaceType"),  # Группировка по месту приготовления
        ("groupRow", "PayTypes"),           # Группировка по типу оплаты
        ("agr", "DishSumInt"),             # Сумма без скидки
        ("agr", "DishDiscountSumInt"),     # Сумма со скидкой
    ]
    
    logger.info(f"🆕 Запрос OLAP отчета SALES, период: {date_from_display} - {date_to_display}")
    
    async with httpx.AsyncClient(base_url=base_url, timeout=60, verify=False) as client:
        url = "/resto/api/reports/olap"
        
        full_url = f"{base_url}{url}?key={token}&report=SALES&from={date_from}&to={date_to}"
        logger.warning(f"🔍 OLAP URL: {full_url}")
        
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
        
        # 🔍 ОТЛАДКА: Проверяем что именно вернул API
        if report_data:
            logger.warning(f"🔍 ПРОВЕРКА ДАННЫХ OLAP API:")
            logger.warning(f"   Первая строка: {report_data[0]}")
            if len(report_data) > 1:
                logger.warning(f"   Последняя строка: {report_data[-1]}")
        
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
    logger.debug(f"Колонки отчета: {df.columns.tolist()}")
    
    # Приводим к числовым типам
    for col in ["DishSumInt", "DishDiscountSumInt"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    
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
    
    is_yandex = df[pay_types_col].astype(str).str.contains("Яндекс.оплата", case=False, na=False)
    is_bar = df[cooking_place_col].astype(str).str.lower() == "бар"
    is_kitchen = df[cooking_place_col].astype(str).str.lower().isin(["кухня", "кухня-пицца", "пицца"])
    
    logger.debug(f"Строк с Яндекс.оплата: {is_yandex.sum()}")
    logger.debug(f"Строк с Бар: {is_bar.sum()}")
    logger.debug(f"Строк с Кухня: {is_kitchen.sum()}")
    
    # Детальное логирование Яндекс оплат для отладки
    if is_yandex.sum() > 0:
        yandex_details = df[is_yandex][[cooking_place_col, pay_types_col, "DishSumInt", "DishDiscountSumInt"]]
        logger.info(f"Яндекс оплаты по местам приготовления:")
        
        # Логируем уникальные типы оплат, которые попали в фильтр
        unique_payment_types = yandex_details[pay_types_col].unique()
        logger.info(f"Типы оплат Яндекс (всего {len(unique_payment_types)}): {list(unique_payment_types)}")
        
        for place in yandex_details[cooking_place_col].unique():
            place_data = yandex_details[yandex_details[cooking_place_col] == place]
            place_sum = place_data["DishSumInt"].sum()
            place_payments = place_data[pay_types_col].unique()
            logger.info(f"  {place}: {place_sum:.2f}₽ (типы: {list(place_payments)})")
    
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
    
    return {
        'bar_revenue': float(bar_revenue),
        'kitchen_revenue': float(kitchen_revenue),
        'delivery_revenue': float(delivery_revenue),
        'yandex_commission': float(yandex_commission_percent),
        'yandex_raw': float(yandex_raw),
        'yandex_fee': float(yandex_fee),
    }


# ════════════════════════════════════════════════════════════════════════════
# ОТКЛЮЧЕНО: Расчет расходных накладных (вынесен в отдельную кнопку)
# ════════════════════════════════════════════════════════════════════════════
async def calculate_writeoffs(date_from: str, date_to: str) -> Dict[str, Any]:
    """
    Получить данные по расходным накладным за период
    
    Args:
        date_from: дата начала в формате YYYY-MM-DD
        date_to: дата конца в формате YYYY-MM-DD
        
    Returns:
        словарь с данными по расходным накладным
    """
    try:
        writeoff_docs = await get_writeoff_documents(date_from, date_to)
        writeoff_sum = sum(doc['sum'] for doc in writeoff_docs)
        writeoff_count = len(writeoff_docs)
        
        # Считаем дни без расходных накладных
        writeoff_dates = set(doc['date'].date() for doc in writeoff_docs)
        from_dt = datetime.strptime(date_from, "%Y-%m-%d")
        to_dt = datetime.strptime(date_to, "%Y-%m-%d")
        total_days = (to_dt - from_dt).days + 1
        days_with_writeoff = len(writeoff_dates)
        days_without_writeoff = total_days - days_with_writeoff
        
        logger.info(f"Расходные накладные: {writeoff_sum:.2f}₽ ({writeoff_count} шт.)")
        logger.info(f"Дней без расходных накладных: {days_without_writeoff} из {total_days}")
        
        return {
            'writeoff_sum': float(writeoff_sum),
            'writeoff_count': writeoff_count,
            'days_without_writeoff': days_without_writeoff,
            'total_days': total_days
        }
    except Exception as e:
        logger.error(f"Ошибка получения расходных накладных: {e}")
        return {
            'writeoff_sum': 0.0,
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


def format_revenue_report(revenue_data: Dict[str, Any], date_from: str, date_to: str, dept_salaries: Dict[str, float] = None) -> str:
    """
    Форматировать отчет по выручке для отправки пользователю
    
    Args:
        revenue_data: данные выручки из calculate_revenue()
        date_from: дата начала периода в формате YYYY-MM-DD
        date_to: дата конца периода в формате YYYY-MM-DD
        dept_salaries: ФОТ по цехам из calculate_salary_by_departments()
        
    Returns:
        отформатированная строка для Telegram
    """
    from datetime import datetime
    
    # Конвертируем даты для отображения
    date_from_display = datetime.strptime(date_from, "%Y-%m-%d").strftime("%d.%m.%Y")
    date_to_display = datetime.strptime(date_to, "%Y-%m-%d").strftime("%d.%m.%Y")
    
    total = revenue_data['bar_revenue'] + revenue_data['kitchen_revenue'] + revenue_data['delivery_revenue']
    
    # Логирование для проверки расчетов
    logger.info(f"📊 Расчет ИТОГО:")
    logger.info(f"  Бар: {revenue_data['bar_revenue']:.2f}₽")
    logger.info(f"  Кухня: {revenue_data['kitchen_revenue']:.2f}₽")
    logger.info(f"  Доставка: {revenue_data['delivery_revenue']:.2f}₽")
    logger.info(f"  ИТОГО: {total:.2f}₽")
    
    text = (
        f"💰 *ОТЧЕТ ПО ВЫРУЧКЕ*\n"
        f"Период: {date_from_display} - {date_to_display}\n\n"
        f"🍹 *БАР*\n"
        f"  Выручка: {revenue_data['bar_revenue']:,.2f} ₽\n\n"
        f"🍕 *КУХНЯ* (Кухня + Пицца)\n"
        f"  Выручка: {revenue_data['kitchen_revenue']:,.2f} ₽\n\n"
        f"🚗 *ДОСТАВКА* (Яндекс)\n"
        f"  Выручка до вычета: {revenue_data['yandex_raw']:,.2f} ₽\n"
        f"  Комиссия ({revenue_data['yandex_commission']}%): -{revenue_data['yandex_fee']:,.2f} ₽\n"
        f"  Выручка после вычета: {revenue_data['delivery_revenue']:,.2f} ₽\n\n"
        f"💵 *ИТОГО ВЫРУЧКА*\n"
        f"  {total:,.2f} ₽\n"
    )
    
    return text.replace(',', ' ')
