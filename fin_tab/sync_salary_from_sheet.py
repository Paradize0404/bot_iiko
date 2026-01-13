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
from fin_tab.scripts.operating_profit import aggregate_pnl, aggregate_salary
from services.position_commission_source import get_position_settings
from scripts.create_fot_sheet import make_title
from services.gsheets_client import GoogleSheetsClient

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def _parse_money(val: Optional[str | float | int]) -> int:
    """Очистить строку с валютой/процентом и вернуть целое число (округление до рублей)."""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(round(float(val)))
    text = str(val).strip()
    if not text:
        return 0
    # убираем пробелы, неразрывные пробелы, валютные/процентные символы и запятые
    text = text.replace("\u00a0", "").replace(" ", "").replace("₽", "").replace("%", "").replace(",", ".")
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
    # Берём нужные колонки: A (ID) .. I (Процент от OP) — столбец I опционален (fallback)
    return client.read_range(f"'{title}'!A2:I1000")


def _build_payload(row: List[str], month_str: str, bonus_override: Optional[int] = None) -> Dict[str, any]:
    # Индексы: 0 ID, 1 Имя, 2 Должность, 3 Начислено, 4 Ставка, 5 Процент, 6 Бонус, 7 Удержания, 8 % от OP
    fix = _parse_money(row[4] if len(row) > 4 else None)
    # «Бонус» из листа теперь идёт в поле percent, приоритет у пересчитанного от OP
    bonus_from_sheet = _parse_money(row[6] if len(row) > 6 else None)
    percent_from_percent_col = _parse_money(row[5] if len(row) > 5 else None)
    percent = bonus_override if bonus_override is not None else (bonus_from_sheet or percent_from_percent_col)

    # «Начислено» (формульная сумма) кладём в поле bonus FinTablo
    accrual_as_bonus = _parse_money(row[3] if len(row) > 3 else None)
    forfeit = _parse_money(row[7] if len(row) > 7 else None)

    total_pay: Dict[str, int] = {
        "fix": fix,
        "percent": percent,
        "bonus": accrual_as_bonus,
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


async def sync_salary_from_sheet(sheet_title: Optional[str] = None, *, send_to_fintablo: bool = True) -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    # Рассчитаем операционную прибыль заранее — нужна для строк, где бонус задаётся как % от OP.
    op_profit = None
    try:
        pnl_by_type, _ = await aggregate_pnl(datetime.now().strftime("%m.%Y"), direction_id=148270)
        salary_by_type = await aggregate_salary(datetime.now().strftime("%m.%Y"), direction_id=148270)

        income = pnl_by_type.get("income", 0.0)
        direct_var = pnl_by_type.get("direct-variable", 0.0)
        direct_prod_items = pnl_by_type.get("direct-production", 0.0)
        commercial_items = pnl_by_type.get("commercial", 0.0)
        administrative_items = pnl_by_type.get("administrative", 0.0)

        def total_salary(block: Dict[str, float]) -> float:
            return (block or {}).get("amount", 0.0) + (block or {}).get("tax", 0.0) + (block or {}).get("fee", 0.0)

        direct_prod_total = direct_prod_items + total_salary(salary_by_type.get("direct-production"))
        commercial_total = commercial_items + total_salary(salary_by_type.get("commercial"))
        administrative_total = administrative_items + total_salary(salary_by_type.get("administrative"))

        op_profit = income - direct_var - direct_prod_total - commercial_total - administrative_total
        logger.info("Операционная прибыль для бонусов (OP): %.2f", op_profit)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Не удалось посчитать операционную прибыль, бонусы от OP не будут обновлены: %s", exc)

    title = sheet_title or _sheet_title_for_today()
    rows = _load_sheet_rows(title)
    month_str = datetime.now().strftime("%m.%Y")

    # Настройки по должностям из листа "Ставки и условия оплат"
    try:
        position_settings = await get_position_settings()
        position_settings = {k.lower(): v for k, v in position_settings.items()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось загрузить настройки ставок по должностям: %s", exc)
        position_settings = {}

    payloads: List[tuple[int, Dict[str, any]]] = []
    op_bonus_updates: List[tuple[int, int]] = []  # row_index (A1) → bonus value for sheet
    for idx, row in enumerate(rows, start=2):
        if not row or len(row) < 1:
            continue
        fin_id_raw = str(row[0]).strip() if row[0] is not None else ""
        if not fin_id_raw.isdigit():
            continue
        employee_id = int(fin_id_raw)
        position = (row[2] if len(row) > 2 else "").strip().lower()
        op_percent = _parse_money(row[8] if len(row) > 8 else None)

        # Если в таблице ставок указан тип "От операционной прибыли" — используем его процент
        pos_settings = position_settings.get(position)
        if pos_settings and pos_settings.get("commission_type") == "operating-profit":
            op_percent = int(round(pos_settings.get("commission_percent", 0) or 0))

        bonus_override = None
        if op_percent and op_profit is not None:
            bonus_override = int(round(op_profit * op_percent / 100))
            logger.info("Бонус от OP %s%% (id=%s): OP=%.2f → %d", op_percent, employee_id, op_profit, bonus_override)
            op_bonus_updates.append((idx, bonus_override))
        elif op_percent and op_profit is None:
            logger.warning("%s%% от OP запрошен для id=%s, но OP не посчитан — пропуск", op_percent, employee_id)
        payloads.append((employee_id, _build_payload(row, month_str, bonus_override)))

    # Проставляем рассчитанный бонус в колонку G листа ФОТ, чтобы его было видно в таблице
    if op_bonus_updates:
        sheet_client = GoogleSheetsClient()
        for row_idx, bonus_value in op_bonus_updates:
            try:
                sheet_client.write_range(f"'{title}'!G{row_idx}", [[bonus_value]])
            except Exception as exc:  # noqa: BLE001
                logger.warning("Не удалось записать бонус в лист ФОТ для строки %s: %s", row_idx, exc)

    if not send_to_fintablo:
        logger.info("Отправка в FinTablo отключена (send_to_fintablo=False), завершаем после обновления листа")
        return 0

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
