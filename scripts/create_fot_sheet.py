"""
Создает лист "ФОТ <Месяц> <Год>" в Google Sheets, если его ещё нет.
Usage:
  python -m scripts.create_fot_sheet [YYYY MM]
Без аргументов берётся текущий месяц.
"""
import sys
from datetime import datetime
from services.gsheets_client import GoogleSheetsClient

MONTHS_RU = [
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]


def make_title(year: int, month: int) -> str:
    month_name = MONTHS_RU[month - 1]
    return f"ФОТ {month_name} {year}"


def ensure_fot_sheet(year: int, month: int) -> str:
    client = GoogleSheetsClient()
    service = client.service
    title = make_title(year, month)

    meta = service.spreadsheets().get(spreadsheetId=client.spreadsheet_id).execute()
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("title") == title:
            print(f"✅ Лист уже существует: {title}")
            return title

    requests = [
        {
            "addSheet": {
                "properties": {
                    "title": title,
                    "index": 0,  # вставляем в начало списка листов
                }
            }
        }
    ]
    try:
        service.spreadsheets().batchUpdate(
            spreadsheetId=client.spreadsheet_id, body={"requests": requests}
        ).execute()
        created = True
    except Exception:
        # Лист уже есть
        created = False

    # Найдём sheetId и применим оформление
    meta_after = service.spreadsheets().get(spreadsheetId=client.spreadsheet_id).execute()
    sheet_id = None
    for s in meta_after.get("sheets", []):
        if s.get("properties", {}).get("title") == title:
            sheet_id = s.get("properties", {}).get("sheetId")
            break

    headers = [
        [
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
    ]
    # Пара пустых строк для ввода данных и строка итого (динамически суммирует всё между шапкой и итого)
    empty_rows = [["" for _ in range(11)] for _ in range(2)]
    # В формуле ROW()-1 берёт строку итого минус 1 => суммирует от A2 до строки перед итогами
    totals = [
        [
            "Итого",
            "",
            "=SUM(INDIRECT(\"C2:C\"&ROW()-1))",
            "=SUM(INDIRECT(\"D2:D\"&ROW()-1))",
            "=SUM(INDIRECT(\"E2:E\"&ROW()-1))",
            "=SUM(INDIRECT(\"F2:F\"&ROW()-1))",
            "=SUM(INDIRECT(\"G2:G\"&ROW()-1))",
            "=SUM(INDIRECT(\"H2:H\"&ROW()-1))",
            "=SUM(INDIRECT(\"I2:I\"&ROW()-1))",
            "=SUM(INDIRECT(\"J2:J\"&ROW()-1))",
            "=SUM(INDIRECT(\"K2:K\"&ROW()-1))",
        ]
    ]
    client.write_range(f"'{title}'!A1:K1", headers)
    client.write_range(f"'{title}'!A2:K3", empty_rows)
    client.write_range(f"'{title}'!A4:K4", totals)

    if sheet_id is not None:
        # Цвета и форматы, чтобы приблизить к макету
        header_color = {"red": 0.9, "green": 0.9, "blue": 0.9}
        total_color_row = {"red": 0.95, "green": 0.95, "blue": 0.95}
        payout_color = {"red": 0.9, "green": 1.0, "blue": 0.9}  # светло-зеленый
        total_color = {"red": 0.97, "green": 0.89, "blue": 0.93}  # светло-розовый
        non_edit_color = {"red": 0.98, "green": 0.92, "blue": 0.95}  # бледно-розовый для формульных/авто
        edit_color = {"red": 1.0, "green": 0.99, "blue": 0.9}  # светло-жёлтый для ручных
        border = {
            "style": "SOLID",
            "width": 1,
            "color": {"red": 0.8, "green": 0.8, "blue": 0.8},
        }

        fmt_reqs = [
            # Заморозка шапки
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            # Единый шрифт Calibri для рабочего блока
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 4,
                        "startColumnIndex": 0,
                        "endColumnIndex": 11,
                    },
                    "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": "Calibri"}}},
                    "fields": "userEnteredFormat.textFormat.fontFamily",
                }
            },
            # Шапка: жирный + заливка
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 11,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": header_color,
                            "textFormat": {"bold": True},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            },
            # Нередактируемые колонки (A-E, K) — бледно-розовый
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 4,
                        "startColumnIndex": 0,
                        "endColumnIndex": 5,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": non_edit_color,
                        }
                    },
                    "fields": "userEnteredFormat.backgroundColor",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 4,
                        "startColumnIndex": 10,  # K
                        "endColumnIndex": 11,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": total_color,
                            "textFormat": {"bold": True},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            },
            # Ручные колонки F,G,H — светло-жёлтый
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 4,
                        "startColumnIndex": 5,
                        "endColumnIndex": 8,
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
            # Выплаты 25/10 — зелёный (ручные)
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 4,
                        "startColumnIndex": 8,  # I,J
                        "endColumnIndex": 10,
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
            # Числовой формат ₽ для C..K (данные + итого)
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 4,
                        "startColumnIndex": 2,
                        "endColumnIndex": 11,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {"type": "NUMBER", "pattern": "#,##0 \"₽\""},
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            },
            # Снимаем жирный шрифт с рабочих строк (только шапка и итого остаются жирными)
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": 3,
                        "startColumnIndex": 0,
                        "endColumnIndex": 11,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": False},
                        }
                    },
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            },
            # Строка итогов (ряд 2) — жирный + серая заливка
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 3,  # строка 4 (итого)
                        "endRowIndex": 4,
                        "startColumnIndex": 0,
                        "endColumnIndex": 11,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": total_color_row,
                            "textFormat": {"bold": True},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            },
            # Грид границы для блока A1:I4
            {
                "updateBorders": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 4,
                        "startColumnIndex": 0,
                        "endColumnIndex": 11,
                    },
                    "top": border,
                    "bottom": border,
                    "left": border,
                    "right": border,
                    "innerHorizontal": border,
                    "innerVertical": border,
                }
            },
            # Ширины колонок (подписи + суммы)
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                    "properties": {"pixelSize": 180},
                    "fields": "pixelSize",
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
                    "properties": {"pixelSize": 140},
                    "fields": "pixelSize",
                }
            },
            {
                "updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": 2, "endIndex": 11},
                    "properties": {"pixelSize": 120},
                    "fields": "pixelSize",
                }
            },
        ]
        service.spreadsheets().batchUpdate(
            spreadsheetId=client.spreadsheet_id, body={"requests": fmt_reqs}
        ).execute()

    if created:
        print(f"✅ Лист создан: {title}")
    else:
        print(f"ℹ️ Лист уже существовал, шапка/итого обновлены: {title}")
    return title


def main():
    if len(sys.argv) >= 3:
        year = int(sys.argv[1])
        month = int(sys.argv[2])
    else:
        now = datetime.now()
        year, month = now.year, now.month
    ensure_fot_sheet(year, month)


if __name__ == "__main__":
    main()
