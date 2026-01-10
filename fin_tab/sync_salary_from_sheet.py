"""Загрузка начислений из ФОТ Google Sheets в FinTablo по FinTablo ID.

Запуск: python -m fin_tab.sync_salary_from_sheet
Позже можно повесить на расписание.
"""
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from dotenv import load_dotenv

from fin_tab.client import FinTabloClient
from scripts.create_fot_sheet import make_title
from services.gsheets_client import GoogleSheetsClient

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _parse_money(val: Optional[str | float | int]) -> int:
    """Очистить строку с валютой и вернуть целое число (округление до рублей)."""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(round(float(val)))
    text = str(val).strip()
    if not text:
        return 0
    # убираем пробелы, неразрывные пробелы, валютные символы и запятые
    text = text.replace("\u00a0", "").replace(" ", "").replace("₽", "").replace(",", ".")
    try:
        return int(round(float(text)))
    except ValueError:
        return 0


def _sheet_title_for_today() -> str:
    today = datetime.now().date()
    return make_title(today.year, today.month)


def _load_sheet_rows(title: str) -> List[List[str]]:
    client = GoogleSheetsClient()
    logger.info("Читаем лист '%s'", title)
    # Берём нужные колонки: A (ID) .. H (Удержания)
    return client.read_range(f"'{title}'!A2:H1000")


def _build_payload(row: List[str], month_str: str) -> Dict[str, any]:
    # Индексы: 0 ID, 1 Имя, 2 Должность, 3 Начислено(formula), 4 Ставка, 5 Бонус, 6 Начисления, 7 Удержания
    fix = _parse_money(row[4] if len(row) > 4 else None)
    percent = _parse_money(row[5] if len(row) > 5 else None)
    bonus = _parse_money(row[6] if len(row) > 6 else None)
    forfeit = _parse_money(row[7] if len(row) > 7 else None)

    total_pay: Dict[str, int] = {
        "fix": fix,
        "percent": percent,
        "bonus": bonus,
        "forfeit": forfeit,
    }

    return {
        "date": month_str,
        "totalPay": total_pay,
        "comment": "Автозагрузка из iiko",
    }


def _extract_total_pay(item: Dict[str, any]) -> Dict[str, int]:
    """Извлекает totalPay из ответа FinTablo, поддерживая объект или список."""
    tp = item.get("totalPay")
    if isinstance(tp, list) and tp:
        tp = tp[0]
    if not isinstance(tp, dict):
        return {"fix": 0, "percent": 0, "bonus": 0, "forfeit": 0}

    def _to_int(v: any) -> int:
        try:
            return int(round(float(v or 0)))
        except Exception:
            return 0

    return {
        "fix": _to_int(tp.get("fix")),
        "percent": _to_int(tp.get("percent")),
        "bonus": _to_int(tp.get("bonus")),
        "forfeit": _to_int(tp.get("forfeit")),
    }


async def sync_salary_from_sheet(sheet_title: Optional[str] = None) -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    title = sheet_title or _sheet_title_for_today()
    rows = _load_sheet_rows(title)
    month_str = datetime.now().strftime("%m.%Y")

    payloads: List[tuple[int, Dict[str, any]]] = []
    for row in rows:
        if not row or len(row) < 1:
            continue
        fin_id_raw = str(row[0]).strip() if row[0] is not None else ""
        if not fin_id_raw.isdigit():
            continue
        employee_id = int(fin_id_raw)
        payloads.append((employee_id, _build_payload(row, month_str)))

    if not payloads:
        logger.info("Нет данных с FinTablo ID для отправки")
        return 0

    logger.info("Готовим отправку %d сотрудников в FinTablo", len(payloads))

    sent = 0
    async with FinTabloClient() as cli:
        for employee_id, payload in payloads:
            try:
                existing = await cli.list_salary(employee_id=employee_id, date=month_str)
                current_tp = _extract_total_pay(existing[0]) if existing else None
                if current_tp and current_tp == payload["totalPay"]:
                    logger.info("⏭️ Пропуск id=%s: суммы уже совпадают", employee_id)
                    continue

                await cli.update_salary(employee_id, payload)
                sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.exception("Не удалось обновить зарплату для id=%s: %s", employee_id, exc)
    logger.info("Готово: отправлено %d сотрудников", sent)
    return sent


async def main() -> int:
    await sync_salary_from_sheet()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
