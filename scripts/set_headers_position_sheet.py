"""
Set Russian headers for the position sheet without touching data.
Usage:
  python -m scripts.set_headers_position_sheet [sheet_name]
"""
import os
import sys
from services.gsheets_client import GoogleSheetsClient

HEADERS = [["Должность", "Тип оплаты", "Ставка оплаты", "Процент", "Тип процента"]]

def main():
    sheet = os.getenv("GOOGLE_SHEETS_POSITION_SHEET", "Ставки и условия оплат")
    if len(sys.argv) > 1:
        sheet = sys.argv[1]
    rng = f"'{sheet}'!A1:E1"
    client = GoogleSheetsClient()
    client.write_range(rng, HEADERS)
    print(f"✅ Headers set for sheet {sheet}")

if __name__ == "__main__":
    main()
