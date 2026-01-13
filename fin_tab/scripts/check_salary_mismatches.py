"""
Diagnostic: compare salary data from FOT Google Sheet vs current values in FinTablo.

Usage:
  PYTHONPATH=. python -m fin_tab.scripts.check_salary_mismatches [MM.YYYY] [--sheet-title "ФОТ Январь 2026"]

Outputs:
- Totals by source (sheet vs FinTablo)
- Missing IDs
- Per-employee mismatches (fix/percent/bonus/forfeit)

Relies on the same parsing logic as sync_salary_from_sheet.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

from fin_tab.client import FinTabloClient
from fin_tab.sync_salary_from_sheet import (
    _build_payload,
    _load_sheet_rows,
    _parse_money,
    _sheet_title_for_today,
    _extract_total_pay,
)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare FOT sheet with FinTablo salaries")
    parser.add_argument("month", nargs="?", help="Month in MM.YYYY, default = current month")
    parser.add_argument("--sheet-title", dest="sheet_title", help="Explicit Google Sheet title")
    return parser.parse_args()


def _build_sheet_map(title: str, month_str: str) -> Dict[int, dict]:
    rows = _load_sheet_rows(title)
    sheet_map: Dict[int, dict] = {}
    for idx, row in enumerate(rows, start=2):
        if not row or len(row) < 1:
            continue
        fin_id_raw = str(row[0]).strip() if row[0] is not None else ""
        if not fin_id_raw.isdigit():
            continue
        employee_id = int(fin_id_raw)
        payload = _build_payload(row, month_str)
        accrual = _parse_money(row[3] if len(row) > 3 else None)
        sheet_map[employee_id] = {
            "row": idx,
            "payload": payload,
            "accrual": accrual,
        }
    return sheet_map


async def _fetch_fin_map(month_str: str) -> Dict[int, dict]:
    fin_map: Dict[int, dict] = {}
    async with FinTabloClient() as cli:
        # Pull all salaries for the month; client has no bulk method, so list by employee ID when needed
        # Here we fetch only for IDs seen in sheet; missing ones will be empty
        pass
    return fin_map


async def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    ns = parse_args()
    month_str = ns.month or datetime.now().strftime("%m.%Y")
    sheet_title = ns.sheet_title or _sheet_title_for_today()

    logger.info("Sheet title: %s | Month: %s", sheet_title, month_str)
    sheet_map = _build_sheet_map(sheet_title, month_str)
    if not sheet_map:
        logger.error("Sheet has no FinTablo IDs; nothing to compare")
        return 1

    ids = sorted(sheet_map.keys())

    fin_map: Dict[int, dict] = {}
    async with FinTabloClient() as cli:
        for emp_id in ids:
            try:
                salaries = await cli.list_salary(employee_id=emp_id, date=month_str)
                fin_map[emp_id] = _extract_total_pay(salaries[0]) if salaries else None
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to fetch salary for id=%s: %s", emp_id, exc)
                fin_map[emp_id] = None

    missing_in_fin = [emp_id for emp_id in ids if not fin_map.get(emp_id)]

    # Totals
    sheet_totals = {"fix": 0, "percent": 0, "bonus": 0, "forfeit": 0}
    fin_totals = {"fix": 0, "percent": 0, "bonus": 0, "forfeit": 0}

    for emp_id in ids:
        s_tp = sheet_map[emp_id]["payload"]["totalPay"]
        for k in sheet_totals:
            sheet_totals[k] += s_tp.get(k, 0) or 0
        f_tp = fin_map.get(emp_id) or {"fix": 0, "percent": 0, "bonus": 0, "forfeit": 0}
        for k in fin_totals:
            fin_totals[k] += f_tp.get(k, 0) or 0

    print("\n== Totals ==")
    print("Sheet   : fix={fix} percent={percent} bonus={bonus} forfeit={forfeit}".format(**sheet_totals))
    print("FinTablo: fix={fix} percent={percent} bonus={bonus} forfeit={forfeit}".format(**fin_totals))

    if missing_in_fin:
        print("\nMissing in FinTablo (no salary for month):", missing_in_fin)

    print("\n== Mismatches ==")
    mismatch_count = 0
    for emp_id in ids:
        sheet_tp = sheet_map[emp_id]["payload"]["totalPay"]
        fin_tp = fin_map.get(emp_id)
        if not fin_tp:
            continue
        diffs = []
        for field in ("fix", "percent", "bonus", "forfeit"):
            if int(sheet_tp.get(field, 0) or 0) != int(fin_tp.get(field, 0) or 0):
                diffs.append(field)
        if diffs:
            mismatch_count += 1
            print(
                f"id={emp_id} row={sheet_map[emp_id]['row']} diffs={','.join(diffs)} | "
                f"sheet={sheet_tp} fin={fin_tp}"
            )

    if mismatch_count == 0 and not missing_in_fin:
        print("No mismatches detected")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
