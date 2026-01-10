"""Вспомогательный скрипт: выводит строки OLAP TRANSACTIONS за период.
Параметры:
    python -m fin_tab.print_transactions [date_from date_to [transaction_type]]
Если даты не указаны — берём с 1-го числа текущего месяца по вчера.
Тип транзакции по умолчанию: INCOMING_INVOICE. Можно передать, например, INCOMING_SERVICE.
"""
import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

from services.purchase_summary import fetch_transactions_rows


def _default_range() -> tuple[str, str]:
    today = date.today()
    start = today.replace(day=1)
    end = today - timedelta(days=1)
    return start.isoformat(), end.isoformat()


class _DecimalEncoder(json.JSONEncoder):
    def default(self, o):  # type: ignore[override]
        from decimal import Decimal

        if isinstance(o, Decimal):
            return float(o)
        return super().default(o)


def _fmt(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), cls=_DecimalEncoder)


async def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")

    if len(sys.argv) >= 3:
        date_from, date_to = sys.argv[1], sys.argv[2]
        trx_type = sys.argv[3] if len(sys.argv) >= 4 else "INCOMING_INVOICE"
    else:
        date_from, date_to = _default_range()
        trx_type = "INCOMING_INVOICE"

    rows = await fetch_transactions_rows(
        date_from=date_from,
        date_to=date_to,
        transaction_types=(trx_type,),
        timeout=90.0,
    )

    filtered_rows = [r for r in rows if r.get("TransactionType") == trx_type]

    total = sum(float(r.get("Sum.Incoming") or 0) for r in filtered_rows)
    print(
        f"Всего строк: {len(filtered_rows)} за период {date_from}..{date_to} тип={trx_type} сумма={total:.2f}"
    )
    for idx, row in enumerate(filtered_rows[:20], start=1):
        print(f"#{idx}: {_fmt(row)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
