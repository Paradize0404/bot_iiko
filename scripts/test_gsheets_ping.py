"""
Quick connectivity test: writes a timestamp into the configured spreadsheet.
Usage:
  python -m scripts.test_gsheets_ping [sheet_name]
Requires env vars:
  GOOGLE_SHEETS_CREDENTIALS_PATH
  GOOGLE_SHEETS_SPREADSHEET_ID
"""
import sys
from services.gsheets_client import GoogleSheetsClient


def main():
    sheet_name = sys.argv[1] if len(sys.argv) > 1 else "Sheet1"
    client = GoogleSheetsClient()
    target = client.ping(sheet_name)
    print(f"✅ Wrote test ping to {target}")


if __name__ == "__main__":
    main()
