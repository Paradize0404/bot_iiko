"""
Тест получения баланса по поставщикам через OLAP отчет по проводкам
Отчет делается на конкретную дату (не период)
"""
import asyncio
import httpx
import xml.etree.ElementTree as ET
from decimal import Decimal
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(message)s')

from iiko.iiko_auth import get_auth_token, get_base_url


def _auto_cast(text):
    """Автоматическое преобразование текста в число или строку"""
    if text is None:
        return None
    try:
        return int(text)
    except:
        try:
            return Decimal(text)
        except:
            return text.strip() if text else None


def parse_xml_report(xml: str):
    """Парсинг XML отчета"""
    root = ET.fromstring(xml)
    rows = []
    for row in root.findall("./r"):
        rows.append({child.tag: _auto_cast(child.text) for child in row})
    return rows


async def get_supplier_balance(date_str: str = None):
    """
    Получить баланс по поставщикам на конкретную дату
    
    Args:
        date_str: дата в формате DD.MM.YYYY (если None, используется сегодня)
    """
    token = await get_auth_token()
    base_url = get_base_url()
    
    if not date_str:
        date_str = datetime.now().strftime("%d.%m.%Y")

    print(f"\n{'='*80}")
    print(f"ДОЛГИ ПО ПОСТАВЩИКАМ на {date_str}")
    print(f"{'='*80}\n")

    # Запрашиваем проводки, группируем по счету и контрагенту
    params = [
        ("key", token),
        ("report", "TRANSACTIONS"),
        ("from", "01.01.2020"),
        ("to", date_str),
        ("groupRow", "Account.Name"),
        ("groupRow", "Counteragent.Name"),
        ("agr", "FinalBalance.Money"),
        ("Account.CounteragentType", "SUPPLIER"),
        ("Counteragent", "SUPPLIER"),  # жёстко фильтруем только контрагентов-поставщиков
        ("Account.Group", "LIABILITIES"),  # оставляем только обязательства
    ]

    async with httpx.AsyncClient(base_url=base_url, timeout=120, verify=False) as client:
        r = await client.get("/resto/api/reports/olap", params=params)
        if r.status_code != 200:
            print(f"❌ Ошибка: {r.text[:1000]}")
            return []

        ct = r.headers.get("content-type", "")
        if ct.startswith("application/json"):
            data = r.json()
            rows = data.get("data", []) or data.get("rows", [])
        elif ct.startswith("application/xml") or ct.startswith("text/xml"):
            rows = parse_xml_report(r.text)
        else:
            print(f"⚠️ Неизвестный формат: {ct}")
            return []

    # Берём только строки по счёту "Задолженность перед поставщиками"
    debt_rows = [row for row in rows if str(row.get("Account.Name")) == "Задолженность перед поставщиками"]

    # Диагностика полей: какие ключи есть в выборке
    if debt_rows:
        keys = sorted(debt_rows[0].keys())
        print("Доступные поля в строке:")
        print(", ".join(keys))

    # Посмотрим, как маркируются строки с подозрительными названиями
    suspicious_markers = [
        "Магазин",
        "Рынок",
        "Палатка",
        "Клемер",
        "Кофейня Театральная",
        "ОФС",
        "ВДНХ",
        "Сбыт",
        "Альцефар",
        "Белова",
        "Мамина",
        "Ресторан Сервис",
    ]
    print("\nПодробности по подозрительным поставщикам:")
    for row in debt_rows:
        name = str(row.get("Counteragent.Name") or "")
        if any(marker in name for marker in suspicious_markers):
            print("--")
            for k in sorted(row.keys()):
                print(f"{k}: {row[k]}")

    # Доп. пробный запрос с расширенной группировкой, чтобы увидеть доступные атрибуты
    extra_group_rows = [
        "Counteragent.Name",
        "Counteragent",          # может дать тип/код контрагента
        "Counteragent.Type",     # пробуем поле типа контрагента
        "Account.Name",
        "Account.Code",          # код счета
        "Account.Group",         # группа счета
        "Account.Type",          # тип счета
    ]
    params_probe = [
        ("key", token),
        ("report", "TRANSACTIONS"),
        ("from", "01.01.2020"),
        ("to", date_str),
        ("agr", "FinalBalance.Money"),
        ("Account.CounteragentType", "SUPPLIER"),
        ("Account.Name", "Задолженность перед поставщиками"),
    ] + [("groupRow", g) for g in extra_group_rows]

    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=120, verify=False) as client:
            r_probe = await client.get("/resto/api/reports/olap", params=params_probe)
            if r_probe.status_code == 200:
                ct = r_probe.headers.get("content-type", "")
                if ct.startswith("application/json"):
                    data_probe = r_probe.json()
                    rows_probe = data_probe.get("data", []) or data_probe.get("rows", [])
                elif ct.startswith("application/xml") or ct.startswith("text/xml"):
                    rows_probe = parse_xml_report(r_probe.text)
                else:
                    rows_probe = []
                if rows_probe:
                    print("\nПоля в пробном запросе (расширенная группировка):")
                    print(", ".join(sorted(rows_probe[0].keys())))
                    # покажем одну подозрительную строку, если есть
                    for row in rows_probe:
                        name = str(row.get("Counteragent.Name") or "")
                        if any(marker in name for marker in suspicious_markers):
                            print("Пример подозрительной строки из пробного запроса:")
                            for k in sorted(row.keys()):
                                print(f"{k}: {row[k]}")
                            break
            else:
                print(f"Пробный запрос неуспешен: {r_probe.status_code} {r_probe.text[:300]}")
    except Exception as e:
        print(f"Ошибка пробного запроса: {e}")

    blacklist_markers = ["Рынок", "Кофейня"]

    agg: dict[str, Decimal] = {}
    for row in debt_rows:
        name = str(row.get("Counteragent.Name") or "N/A")
        if any(marker in name for marker in blacklist_markers):
            continue  # исключаем рынки/кофейни
        val_raw = row.get("FinalBalance.Money", 0) or 0
        try:
            val = Decimal(str(val_raw).replace(",", "."))
        except Exception:
            val = Decimal(0)
        if val <= 0:
            continue  # отбрасываем нулевые и отрицательные
        agg[name] = agg.get(name, Decimal(0)) + val

    top = sorted(agg.items(), key=lambda x: x[1], reverse=True)
    print(f"Найдено поставщиков: {len(agg)}")
    print(f"{'Поставщик':<50} {'Долг (FinalBalance)':>20}")
    print("-" * 80)
    for name, bal in top:
        if bal == 0:
            continue
        print(f"{name:<50} {bal:>20,.2f}₽")
    total = sum(agg.values(), Decimal(0))
    print("-" * 80)
    print(f"ИТОГО по всем: {total:,.2f}₽")

    target = "БАЛТСМАК ПЛЮС ООО"
    if target in agg:
        print(f"\nПроверка {target}: {agg[target]:,.2f}₽ (только счёт 'Задолженность перед поставщиками')")
    else:
        print(f"\n{target} не найден")
    return debt_rows


async def main():
    # Пример: отчет на 04.12.2025
    await get_supplier_balance("04.12.2025")


if __name__ == "__main__":
    asyncio.run(main())
