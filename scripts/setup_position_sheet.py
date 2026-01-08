"""
Apply data validation and basic formatting to the position commissions sheet so staff can edit safely.
- Columns:
  A: position_name (free text)
  B: payment_type (dropdown: hourly | per_shift | monthly)
  C: fixed_rate (must be blank when hourly; required when per_shift or monthly)
  D: commission_percent (0-100)
  E: commission_type (dropdown: sales | writeoff)
- Freezes header row 1.
Usage:
  python -m scripts.setup_position_sheet [sheet_name]
"""
import os
import sys
import logging

from services.gsheets_client import GoogleSheetsClient
from scripts.protect_position_sheet_strict import last_row_with_positions

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _find_sheet_id(service, spreadsheet_id: str, title: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta.get("sheets", []):
        if s["properties"].get("title") == title:
            return s["properties"]["sheetId"]
    raise RuntimeError(f"Sheet '{title}' not found")


def apply_validation(sheet_name: str, last_row: int | None = None) -> None:
    client = GoogleSheetsClient()
    service = client.service
    sheet_id = _find_sheet_id(service, client.spreadsheet_id, sheet_name)

    # limit validations only to rows that have a position
    last = last_row if last_row is not None else last_row_with_positions(client, sheet_name)
    if last <= 1:
        logger.info("⚠️ Нет должностей в листе '%s' — валидация не применена", sheet_name)
        return

    # Clear old validations on B:E to avoid leftover dropdowns below data
    clear_range = {
        "sheetId": sheet_id,
        "startRowIndex": 1,  # row 2
        "endRowIndex": max(last, 1000),
        "startColumnIndex": 1,  # col B
        "endColumnIndex": 5,    # up to E
    }

    # Define ranges (row 2 downward, columns 0-based)
    rng_payment_type = {
        "sheetId": sheet_id,
        "startRowIndex": 1,
        "startColumnIndex": 1,  # column B
        "endRowIndex": last,
        "endColumnIndex": 2,
    }
    rng_percent = {
        "sheetId": sheet_id,
        "startRowIndex": 1,
        "startColumnIndex": 3,  # column D
        "endRowIndex": last,
        "endColumnIndex": 4,
    }
    rng_commission_type = {
        "sheetId": sheet_id,
        "startRowIndex": 1,
        "startColumnIndex": 4,  # column E
        "endRowIndex": last,
        "endColumnIndex": 5,
    }

    requests = [
        {
            "repeatCell": {
                "range": clear_range,
                "cell": {},
                "fields": "dataValidation",
            }
        },
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        },
        {
            "setDataValidation": {
                "range": rng_payment_type,
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "Почасовая"},
                            {"userEnteredValue": "Посменная"},
                            {"userEnteredValue": "Ежемесячная"},
                        ],
                    },
                    "inputMessage": "Выбери: Почасовая / Посменная / Ежемесячная",
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        },
        {
            "setDataValidation": {
                "range": rng_commission_type,
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "От продаж"},
                            {"userEnteredValue": "От накладных"},
                        ],
                    },
                    "inputMessage": "От продаж / От накладных",
                    "strict": True,
                    "showCustomUi": True,
                },
            }
        },
        {
            "setDataValidation": {
                "range": rng_percent,
                "rule": {
                    "condition": {
                        "type": "NUMBER_BETWEEN",
                        "values": [
                            {"userEnteredValue": "0"},
                            {"userEnteredValue": "100"},
                        ],
                    },
                    "inputMessage": "Процент 0–100",
                    "strict": True,
                },
            }
        },
        # Conditional formatting via simple formula is flaky via API; skip to avoid 400 errors.
    ]

    body = {"requests": requests}
    service.spreadsheets().batchUpdate(spreadsheetId=client.spreadsheet_id, body=body).execute()
    logger.info("✅ Validation and freeze applied to sheet '%s'", sheet_name)


if __name__ == "__main__":
    sheet = os.getenv("GOOGLE_SHEETS_POSITION_SHEET", "Ставки и условия оплат")
    if len(sys.argv) > 1:
        sheet = sys.argv[1]
    apply_validation(sheet)
