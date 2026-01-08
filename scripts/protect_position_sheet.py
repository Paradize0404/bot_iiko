"""
Protect column A and header row 1 in the positions sheet to prevent edits by staff.
- Removes prior protections with the same descriptions, then adds fresh ones.
Usage:
  python -m scripts.protect_position_sheet [sheet_name]
"""
import os
import sys
from services.gsheets_client import GoogleSheetsClient

COL_PROTECT_DESC = "Protect column A (positions)"
HEADER_PROTECT_DESC = "Protect header row"


def find_sheet_id(service, spreadsheet_id: str, title: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("title") == title:
            return s["properties"]["sheetId"]
    raise RuntimeError(f"Sheet '{title}' not found")


def cleanup_existing(service, spreadsheet_id: str, sheet_id: int):
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties,sheets.protectedRanges",
    ).execute()
    deletes = []
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("sheetId") != sheet_id:
            continue
        for pr in s.get("protectedRanges", []) or []:
            desc = pr.get("description", "")
            if desc in (COL_PROTECT_DESC, HEADER_PROTECT_DESC):
                deletes.append(pr.get("protectedRangeId"))
    if not deletes:
        return []
    reqs = [
        {"deleteProtectedRange": {"protectedRangeId": pr_id}} for pr_id in deletes if pr_id is not None
    ]
    if reqs:
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": reqs},
        ).execute()
    return deletes


def add_protections(service, spreadsheet_id: str, sheet_id: int):
    requests = [
        {
            "addProtectedRange": {
                "protectedRange": {
                    "description": COL_PROTECT_DESC,
                    "range": {
                        "sheetId": sheet_id,
                        "startColumnIndex": 0,
                        "endColumnIndex": 1,
                    },
                    "warningOnly": False,
                }
            }
        },
        {
            "addProtectedRange": {
                "protectedRange": {
                    "description": HEADER_PROTECT_DESC,
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                    },
                    "warningOnly": False,
                }
            }
        },
    ]
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": requests},
    ).execute()


def main():
    sheet = os.getenv("GOOGLE_SHEETS_POSITION_SHEET", "Ставки и условия оплат")
    if len(sys.argv) > 1:
        sheet = sys.argv[1]

    client = GoogleSheetsClient()
    service = client.service
    sid = find_sheet_id(service, client.spreadsheet_id, sheet)
    removed = cleanup_existing(service, client.spreadsheet_id, sid)
    add_protections(service, client.spreadsheet_id, sid)
    print(f"✅ Protected column A and header row on sheet '{sheet}'. Removed old protections: {removed}")


if __name__ == "__main__":
    main()
