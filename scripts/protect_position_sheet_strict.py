"""
Protect the sheet so only B..E on existing position rows are editable; everything else is locked.
- Detects last non-empty row in column A (positions).
- Unprotected range: B2:E<last_row> (if no data, nothing is unprotected).
- Adds a sheet-wide protected range with that unprotected slice.
- Removes previous protection with the same description.
Usage:
  python -m scripts.protect_position_sheet_strict [sheet_name]
"""
import os
import sys
from services.gsheets_client import GoogleSheetsClient

PROTECT_DESC = "Lock all except B-E with positions"


def find_sheet_id(service, spreadsheet_id: str, title: str) -> int:
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("title") == title:
            return s["properties"]["sheetId"]
    raise RuntimeError(f"Sheet '{title}' not found")


def last_row_with_positions(client: GoogleSheetsClient, sheet: str) -> int:
    values = client.read_range(f"'{sheet}'!A2:A")
    last = 1  # header row index (1-based); if no data -> returns 1
    for idx, row in enumerate(values, start=2):
        if row and row[0]:
            last = idx
    return last


def remove_old_protection(service, spreadsheet_id: str, sheet_id: int):
    meta = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties,sheets.protectedRanges",
    ).execute()
    ids = []
    for s in meta.get("sheets", []):
        if s.get("properties", {}).get("sheetId") != sheet_id:
            continue
        for pr in s.get("protectedRanges", []) or []:
            if pr.get("description") == PROTECT_DESC:
                pr_id = pr.get("protectedRangeId")
                if pr_id is not None:
                    ids.append(pr_id)
    if not ids:
        return
    reqs = [{"deleteProtectedRange": {"protectedRangeId": pid}} for pid in ids]
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": reqs}).execute()


def apply_protection(service, spreadsheet_id: str, sheet_id: int, last_row: int):
    # Unprotected range only if we have data beyond header
    unprotected = []
    if last_row > 1:
        unprotected.append({
            "sheetId": sheet_id,
            "startRowIndex": 1,  # row 2
            "endRowIndex": last_row,  # exclusive
            "startColumnIndex": 1,  # col B
            "endColumnIndex": 5,   # col F exclusive, so up to E
        })
    requests = [
        {
            "addProtectedRange": {
                "protectedRange": {
                    "description": PROTECT_DESC,
                    "range": {"sheetId": sheet_id},  # whole sheet
                    "unprotectedRanges": unprotected,
                    "warningOnly": False,
                }
            }
        }
    ]
    service.spreadsheets().batchUpdate(spreadsheetId=spreadsheet_id, body={"requests": requests}).execute()


def main():
    sheet = os.getenv("GOOGLE_SHEETS_POSITION_SHEET", "Ставки и условия оплат")
    if len(sys.argv) > 1:
        sheet = sys.argv[1]

    client = GoogleSheetsClient()
    service = client.service
    sheet_id = find_sheet_id(service, client.spreadsheet_id, sheet)
    last = last_row_with_positions(client, sheet)
    remove_old_protection(service, client.spreadsheet_id, sheet_id)
    apply_protection(service, client.spreadsheet_id, sheet_id, last + 1)  # endRowIndex exclusive
    print(f"✅ Protected sheet '{sheet}'. Editable only B2:E{last if last>1 else 2} (rows with должности).")


if __name__ == "__main__":
    main()
