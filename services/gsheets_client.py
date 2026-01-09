"""
Google Sheets helper built around service-account credentials.
Reads credentials and spreadsheet ID from environment variables:
- GOOGLE_SHEETS_SPREADSHEET_ID: spreadsheet ID from the URL (required)
- One of the credential inputs (checked in this order):
  * GOOGLE_SHEETS_CREDENTIALS_JSON: raw JSON string
  * GOOGLE_SHEETS_CREDENTIALS_B64: base64-encoded JSON
  * GOOGLE_SHEETS_CREDENTIALS_PATH: absolute path to JSON key file
"""
import base64
import json
import os
import datetime as dt
from typing import Any, Dict, List

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class GoogleSheetsClient:
    def __init__(self) -> None:
        spreadsheet_id = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
        if not spreadsheet_id:
            raise RuntimeError("GOOGLE_SHEETS_SPREADSHEET_ID is not set")

        credentials = self._load_credentials()
        self.spreadsheet_id = spreadsheet_id
        self.service = build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def _load_credentials(self) -> Credentials:
        raw_json = os.getenv("GOOGLE_SHEETS_CREDENTIALS_JSON")
        b64_json = os.getenv("GOOGLE_SHEETS_CREDENTIALS_B64")
        cred_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS_PATH")

        info: Dict[str, Any] | None = None
        if raw_json:
            info = json.loads(raw_json)
        elif b64_json:
            decoded = base64.b64decode(b64_json).decode("utf-8")
            info = json.loads(decoded)
        elif cred_path:
            return Credentials.from_service_account_file(cred_path, scopes=SCOPES)

        if info is not None:
            return Credentials.from_service_account_info(info, scopes=SCOPES)

        raise RuntimeError(
            "Provide Google credentials via GOOGLE_SHEETS_CREDENTIALS_JSON, "
            "GOOGLE_SHEETS_CREDENTIALS_B64, or GOOGLE_SHEETS_CREDENTIALS_PATH"
        )

    def read_range(self, range_a1: str) -> List[List[Any]]:
        sheet = self.service.spreadsheets()
        resp = sheet.values().get(spreadsheetId=self.spreadsheet_id, range=range_a1).execute()
        return resp.get("values", [])

    def write_range(self, range_a1: str, values: List[List[Any]]) -> None:
        sheet = self.service.spreadsheets()
        sheet.values().update(
            spreadsheetId=self.spreadsheet_id,
            range=range_a1,
            valueInputOption="USER_ENTERED",
            body={"values": values},
        ).execute()

    def clear_range(self, range_a1: str) -> None:
        sheet = self.service.spreadsheets()
        sheet.values().clear(
            spreadsheetId=self.spreadsheet_id,
            range=range_a1,
            body={},
        ).execute()

    def append_row(self, sheet_name: str, row: List[Any]) -> None:
        sheet = self.service.spreadsheets()
        sheet.values().append(
            spreadsheetId=self.spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row]},
        ).execute()

    def ping(self, sheet_name: str = "Sheet1") -> str:
        """Write a timestamp to prove connectivity; returns range used."""
        stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_sheet = sheet_name if sheet_name.startswith("'") else f"'{sheet_name}'"
        target_range = f"{safe_sheet}!A1"
        self.write_range(target_range, [[f"Ping from bot at {stamp}"]])
        return target_range
