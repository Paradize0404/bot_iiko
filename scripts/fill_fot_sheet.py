"""
Автозаполнение листа ФОТ на основе отчёта зарплат из iiko.
- 1-го числа — только создаём пустой лист.
- Со 2-го числа ежедневно в 12:00 заполняем период с 1-го числа по вчерашний день.
- Колонки: Ставка ← "Оплата" из отчёта, Бонус ← "Комиссия", Удержания ← штрафы; "Начислено" = D+E+F-G.
- Строка "Итого" суммирует фактический блок данных.
Usage:
    python -m scripts.fill_fot_sheet
"""
import asyncio
import logging
from datetime import datetime, timedelta
import xml.etree.ElementTree as ET

from iiko.iiko_auth import get_auth_token, get_base_url
from services.gsheets_client import GoogleSheetsClient
from scripts.create_fot_sheet import make_title, ensure_fot_sheet
from services.salary_from_iiko import fetch_salary_from_iiko


HEADERS = [
    "FinTablo ID",
    "Сотрудник",
    "Должность",
    "Начислено, р.",
    "Ставка",
    "Бонус",
    "Начисления",
    "Удержания",
    "Аванс",
    "25 Выплата",
    "10 Выплата",
    "К выплате, р.",
]


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def fetch_employees_with_positions_filtered(from_date: str, to_date: str) -> list[tuple[str, str]]:
    """
    Возвращает сотрудников, которые попали бы в зарплатный расчёт:
    - активные в iiko
    - есть attendance в периоде ИЛИ payment_type == monthly
    """
    logger.info("Запрашиваем сотрудников с ролями и посещениями: %s..%s", from_date, to_date)
    token = await get_auth_token()
    base_url = get_base_url()

    import httpx

    async def fetch_roles():
        roles_url = f"{base_url}/resto/api/employees/roles"
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            resp = await client.get(roles_url, headers={"Cookie": f"key={token}"})
        resp.raise_for_status()
        tree = ET.fromstring(resp.text)
        roles = {}
        for role in tree.findall(".//role"):
            code = role.findtext("code")
            name = role.findtext("name")
            if code and name:
                roles[code] = name.strip()
        return roles

    async def fetch_employees():
        emp_url = f"{base_url}/resto/api/employees"
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            resp = await client.get(emp_url, headers={"Cookie": f"key={token}"}, params={"includeDeleted": "false"})
        resp.raise_for_status()
        return resp.text

    async def fetch_attendance():
        att_url = f"{base_url}/resto/api/employees/attendance/"
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            resp = await client.get(
                att_url,
                headers={"Cookie": f"key={token}"},
                params={"from": from_date, "to": to_date},
            )
        resp.raise_for_status()
        return resp.text

    roles_map, emp_xml, att_xml = await asyncio.gather(fetch_roles(), fetch_employees(), fetch_attendance())
    logger.debug("Получено ролей: %d", len(roles_map))

    # attendance: соберём employeeId у кого есть записи
    att_tree = ET.fromstring(att_xml)
    has_attendance: set[str] = set()
    for att in att_tree.findall(".//attendance"):
        emp_id = att.findtext("employeeId")
        if emp_id:
            has_attendance.add(emp_id)

    emp_tree = ET.fromstring(emp_xml)
    result: list[tuple[str, str]] = []
    for emp in emp_tree.findall(".//employee"):
        deleted = emp.findtext("deleted", "false") == "true"
        if deleted:
            continue
        emp_id = emp.findtext("id")
        name = (emp.findtext("name") or "").strip()
        if not emp_id or not name:
            continue

        # payment type: если monthly, включаем даже без attendance
        payment_type = (emp.findtext("paymentType") or "").strip().lower()

        if emp_id not in has_attendance and payment_type != "monthly":
            continue

        position_code = None
        role_codes_element = emp.find("roleCodes")
        if role_codes_element is not None:
            role_code = role_codes_element.find("string")
            if role_code is not None and role_code.text:
                position_code = role_code.text
        if not position_code:
            position_code = emp.findtext("mainRoleCode")
        position = roles_map.get(position_code, "") if position_code else ""
        result.append((name, position))

    # сортируем по имени
    sorted_res = sorted(result, key=lambda x: x[0].lower())
    logger.info("Отфильтровано сотрудников: %d", len(sorted_res))
    return sorted_res


def build_totals_row(row_idx: int) -> list[str]:
    """Строка итого для переменного числа строк."""

    return [
        "Итого",  # A
        "",  # B
        "",  # C
        f"=SUM(D2:D{row_idx - 1})",  # D
        f"=SUM(E2:E{row_idx - 1})",  # E
        f"=SUM(F2:F{row_idx - 1})",  # F
        f"=SUM(G2:G{row_idx - 1})",  # G
        f"=SUM(H2:H{row_idx - 1})",  # H
        f"=SUM(I2:I{row_idx - 1})",  # I
        f"=SUM(J2:J{row_idx - 1})",  # J
        f"=SUM(K2:K{row_idx - 1})",  # K
        f"=SUM(L2:L{row_idx - 1})",  # L
    ]


def _to_float(val: str | float | int | None) -> float:
    if val is None:
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    text = str(val).strip()
    if not text:
        return 0.0
    text = text.replace("\u00a0", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def ensure_fintablo_column(client: GoogleSheetsClient, service, sheet_id: int, title: str) -> None:
    """Убедиться, что первый столбец — FinTablo ID, и шапка обновлена под 12 колонок."""

    headers_raw = client.read_range(f"'{title}'!A1:L1")
    header_row = headers_raw[0] if headers_raw else []
    has_id = header_row and (header_row[0] or "").strip() == "FinTablo ID"

    if not has_id:
        logger.info("Добавляем столбец FinTablo ID в лист '%s'", title)
        service.spreadsheets().batchUpdate(
            spreadsheetId=client.spreadsheet_id,
            body={
                "requests": [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": sheet_id,
                                "dimension": "COLUMNS",
                                "startIndex": 0,
                                "endIndex": 1,
                            },
                            "inheritFromBefore": False,
                        }
                    }
                ]
            },
        ).execute()

    logger.debug("Обновляем шапку A1:L1 под FinTablo ID и 12 колонок")
    client.write_range(f"'{title}'!A1:L1", [HEADERS])


def write_sheet(rows: list[tuple[str, str, float, float, float]], title: str) -> None:
    logger.info("Пишем лист '%s' строк: %d", title, len(rows))
    client = GoogleSheetsClient()
    service = client.service

    logger.debug("Читаем метаданные таблицы")
    meta = service.spreadsheets().get(spreadsheetId=client.spreadsheet_id).execute()
    sheet_id = None
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        if props.get("title") == title:
            sheet_id = props.get("sheetId")
            break
    if sheet_id is None:
        logger.error("Не найден лист '%s'", title)
        raise RuntimeError(f"Sheet '{title}' not found")

    ensure_fintablo_column(client, service, sheet_id, title)

    totals_row_index = len(rows) + 2  # 1 (header) + data + итого

    # Сохраняем ручные значения, привязывая к сотруднику (чтобы переносились при смещении)
    manual_by_key: dict[tuple[str, str], tuple[str, str, str, str, str, str]] = {}
    manual_by_name: dict[str, tuple[str, str, str, str, str, str]] = {}
    logger.debug("Читаем существующие данные A2:L1000 для сохранения ручных значений")
    existing_full = client.read_range(f"'{title}'!A2:L1000")
    for row in existing_full:
        if not row or len(row) < 1:
            continue
        fin_id = row[0] if len(row) >= 1 else ""
        name_raw = row[1] if len(row) >= 2 else ""
        pos_raw = row[2] if len(row) >= 3 else ""
        name_key = name_raw.strip().lower()
        pos_key = pos_raw.strip().lower()
        manual_tuple = (
            fin_id,
            row[6] if len(row) >= 7 else "",
            row[7] if len(row) >= 8 else "",
            row[8] if len(row) >= 9 else "",
            row[9] if len(row) >= 10 else "",
            row[10] if len(row) >= 11 else "",
        )
        key = (name_key, pos_key)
        if name_key:
            manual_by_key.setdefault(key, manual_tuple)
            manual_by_name.setdefault(name_key, manual_tuple)

    data_rows: list[list[str | float]] = []
    for idx, (name, position, rate, bonus, penalty) in enumerate(rows, start=2):
        name_key = (name or "").strip().lower()
        pos_key = (position or "").strip().lower()
        manual_tuple = manual_by_key.get((name_key, pos_key)) or manual_by_name.get(name_key) or ("", "", "", "", "", "")
        fin_id_raw, manual_acc_raw, manual_penalty_raw, advance_raw, payout_25_raw, payout_10_raw = manual_tuple

        manual_acc = manual_acc_raw if manual_acc_raw != "" else ""
        if manual_penalty_raw != "":
            manual_penalty = manual_penalty_raw
        elif penalty:
            manual_penalty = penalty
        else:
            manual_penalty = ""
        advance = advance_raw if advance_raw != "" else ""
        payout_25 = payout_25_raw if payout_25_raw != "" else ""
        payout_10 = payout_10_raw if payout_10_raw != "" else ""

        is_freelancer = "фриланс" in (position or "").lower()
        total_formula = f"=ROUND(E{idx}+F{idx}+G{idx}-H{idx})"
        payout_formula = f"=ROUND(D{idx}-J{idx}-K{idx})"
        if is_freelancer:
            payout_10 = f"=D{idx}"
            logger.debug("Фрилансер: %s — авто-выплата K=D%d", name, idx)

        data_rows.append(
            [
                fin_id_raw,
                name,
                position,
                total_formula,
                round(float(rate or 0), 2),
                round(float(bonus or 0), 2),
                manual_acc,
                manual_penalty if manual_penalty else penalty,
                advance,
                payout_25,
                payout_10,
                payout_formula,
            ]
        )

    all_rows = data_rows + [build_totals_row(totals_row_index)]

    # Чистим старый диапазон (во избежание дублей строк итого и "хвостов" от прошлых запусков)
    logger.debug("Очищаем диапазон перед записью A2:L1000")
    client.clear_range(f"'{title}'!A2:L1000")

    # Записываем данные
    logger.debug("Пишем диапазон A2:L%d", totals_row_index)
    client.write_range(f"'{title}'!A2:L{totals_row_index}", all_rows)

    # Форматирование
    header_color = {"red": 0.9, "green": 0.9, "blue": 0.9}
    total_color_row = {"red": 0.95, "green": 0.95, "blue": 0.95}
    payout_color = {"red": 0.9, "green": 1.0, "blue": 0.9}
    total_color = {"red": 0.97, "green": 0.89, "blue": 0.93}
    non_edit_color = {"red": 0.98, "green": 0.92, "blue": 0.95}
    edit_color = {"red": 1.0, "green": 0.99, "blue": 0.9}
    border = {"style": "SOLID", "width": 1, "color": {"red": 0.8, "green": 0.8, "blue": 0.8}}

    fmt_reqs = [
        # Очистка заливок во всём листе
        {
            "repeatCell": {
                "range": {"sheetId": sheet_id, "startRowIndex": 0, "startColumnIndex": 0},
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat(backgroundColor)",
            }
        },
        # Шрифт Calibri для блока A1:L
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": totals_row_index,
                    "startColumnIndex": 0,
                    "endColumnIndex": 12,
                },
                "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": "Calibri"}}},
                "fields": "userEnteredFormat.textFormat.fontFamily",
            }
        },
        # Шапка (серый + жирный)
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 12,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": header_color,
                        "textFormat": {"bold": True, "fontFamily": "Calibri"},
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        },
        # Нередактируемые колонки D-F (Начислено, Ставка, Бонус)
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": totals_row_index,
                    "startColumnIndex": 3,
                    "endColumnIndex": 6,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": non_edit_color}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        },
        # Ручные колонки G,H,I — светло-жёлтый
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": totals_row_index,
                    "startColumnIndex": 6,
                    "endColumnIndex": 9,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": edit_color,
                        "numberFormat": {"type": "NUMBER", "pattern": "#,##0 \"₽\""},
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,numberFormat)",
            }
        },
        # Выплаты 25/10 (J,K) — зелёный
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": totals_row_index,
                    "startColumnIndex": 9,
                    "endColumnIndex": 11,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": payout_color,
                        "numberFormat": {"type": "NUMBER", "pattern": "#,##0 \"₽\""},
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,numberFormat)",
            }
        },
        # Колонка L — розовый + жирный
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": totals_row_index,
                    "startColumnIndex": 11,
                    "endColumnIndex": 12,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": total_color,
                        "numberFormat": {"type": "NUMBER", "pattern": "#,##0 \"₽\""},
                        "textFormat": {"bold": True, "fontFamily": "Calibri"},
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,numberFormat,textFormat)",
            }
        },
        # Числовой формат ₽ для D..L
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": totals_row_index,
                    "startColumnIndex": 3,
                    "endColumnIndex": 12,
                },
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0 \"₽\""}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        },
        # Снимаем жирный шрифт с рабочих строк (кроме колонки L)
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": totals_row_index - 1,
                    "startColumnIndex": 0,
                    "endColumnIndex": 11,
                },
                "cell": {"userEnteredFormat": {"textFormat": {"bold": False, "fontFamily": "Calibri"}}},
                "fields": "userEnteredFormat.textFormat",
            }
        },
        # Строка итого (серая + жирная)
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": totals_row_index - 1,
                    "endRowIndex": totals_row_index,
                    "startColumnIndex": 0,
                    "endColumnIndex": 12,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": total_color_row,
                        "textFormat": {"bold": True, "fontFamily": "Calibri"},
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        },
        # Границы по фактическому блоку A1:L<totals_row_index>
        {
            "updateBorders": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0,
                    "endRowIndex": totals_row_index,
                    "startColumnIndex": 0,
                    "endColumnIndex": 12,
                },
                "top": border,
                "bottom": border,
                "left": border,
                "right": border,
                "innerHorizontal": border,
                "innerVertical": border,
            }
        },
    ]

    logger.debug("Применяем форматирование %d блоков", len(fmt_reqs))
    service.spreadsheets().batchUpdate(
        spreadsheetId=client.spreadsheet_id, body={"requests": fmt_reqs}
    ).execute()

    # Текстовый формат для ID/ФИО/должности
    client.set_column_text_format(title, "A")
    client.set_column_text_format(title, "B")
    client.set_column_text_format(title, "C")

    # Ширины столбцов: A-C чуть шире, остальные компактнее
    width_requests = [
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 3},
                "properties": {"pixelSize": 130},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 3, "endIndex": 12},
                "properties": {"pixelSize": 110},
                "fields": "pixelSize",
            }
        },
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=client.spreadsheet_id, body={"requests": width_requests}).execute()


async def main():
    today = datetime.now().date()
    title = make_title(today.year, today.month)
    ensure_fot_sheet(today.year, today.month)

    # 1-го числа создаём пустой лист и выходим
    if today.day == 1:
        logger.info("1-е число: создаём/проверяем лист и выходим")
        print(f"✅ Лист '{title}' создан/проверен, заполнение не требуется (1-е число)")
        return

    period_from = today.replace(day=1).isoformat()
    period_to = (today - timedelta(days=1)).isoformat()

    # Получаем зарплатные данные и агрегируем по сотруднику
    logger.info("Запрашиваем зарплатные данные %s..%s", period_from, period_to)
    salary_data = await fetch_salary_from_iiko(period_from, period_to)
    rows: list[tuple[str, str, float, float, float]] = []

    if salary_data:
        aggregated = {}
        for key, rec in salary_data.items():
            emp_id = key.split("_", 1)[0]
            entry = aggregated.setdefault(
                emp_id,
                {
                    "name": rec.get("name", ""),
                    "position": rec.get("position", ""),
                    "rate": 0.0,
                    "bonus": 0.0,
                    "penalty": 0.0,
                    "latest_end": rec.get("period_end"),
                },
            )

            entry["rate"] += float(rec.get("regular_payment", 0) or 0)
            entry["bonus"] += float(rec.get("bonus", 0) or 0)
            entry["penalty"] += float(rec.get("penalty", 0) or 0)

            period_end = rec.get("period_end")
            latest_end = entry.get("latest_end")
            if period_end and (latest_end is None or period_end > latest_end):
                entry["position"] = rec.get("position", entry["position"])
                entry["latest_end"] = period_end

        rows = [
            (v["name"], v["position"], v["rate"], v["bonus"], v["penalty"])
            for v in aggregated.values()
        ]
        rows.sort(key=lambda x: x[0].lower())
        logger.info("Собрано сотрудников для записи: %d", len(rows))

    else:
        # Fallback: оставляем лист пустым, но пишем шапку/итого
        logger.warning("Нет данных зарплат за период, лист оставлен пустым")
        print("⚠️ Нет данных зарплат за период, лист оставлен пустым")

    write_sheet(rows, title)
    logger.info("Готово: записано %d строк", len(rows))
    print(f"✅ Заполнено строк: {len(rows)} за период {period_from}–{period_to} в лист '{title}'")


if __name__ == "__main__":
    asyncio.run(main())
