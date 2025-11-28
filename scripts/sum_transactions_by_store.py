from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from decimal import Decimal

from services.purchase_summary import (
    DEFAULT_TRANSACTION_CODES,
    DEFAULT_TRANSACTION_TYPES,
    PurchaseSummary,
    get_purchase_summary,
)


def parse_args() -> argparse.Namespace:
    today = datetime.now().date()
    default_start = today.replace(day=1)
    parser = argparse.ArgumentParser(
        description="Суммы приходных накладных по складам через OLAP TRANSACTIONS",
    )
    parser.add_argument(
        "--from-date",
        dest="date_from",
        default=default_start.strftime("%Y-%m-%d"),
        help="Дата начала периода (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--to-date",
        dest="date_to",
        default=today.strftime("%Y-%m-%d"),
        help="Дата конца периода (YYYY-MM-DD)",
    )
    parser.add_argument(
        "--transaction-type",
        dest="transaction_types",
        action="append",
        help="Тип транзакции OLAP (по умолчанию INCOMING_INVOICE)",
    )
    parser.add_argument(
        "--olap-transaction-code",
        dest="transaction_codes",
        action="append",
        help="Значение поля TransactionType (по умолчанию INVOICE)",
    )
    parser.add_argument(
        "--store",
        action="append",
        default=[],
        help="Ограничить отчёт указанными складами",
    )
    parser.add_argument(
        "--include-all-accounts",
        action="store_true",
        help="Не ограничивать список счетов справочником складов",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Показать детализацию по складу и контрагенту",
    )
    parser.add_argument(
        "--no-supplier-summary",
        action="store_true",
        help="Не печатать суммы по контрагентам",
    )
    parser.add_argument(
        "--no-store-summary",
        action="store_true",
        help="Не печатать суммы по складам",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="HTTP timeout",
    )
    return parser.parse_args()


def fmt_currency(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


def print_suppliers(summary: PurchaseSummary) -> None:
    if not summary.supplier_totals:
        print("\nСводка по контрагентам недоступна")
        return
    print("\nСуммы по контрагентам:")
    for supplier, amount in sorted(summary.supplier_totals.items(), key=lambda item: item[1], reverse=True):
        print(f" - {supplier}: {fmt_currency(amount)}")


def print_stores(summary: PurchaseSummary) -> None:
    if not summary.store_totals:
        return
    print("\nСуммы по складам:")
    for store, amount in sorted(summary.store_totals.items(), key=lambda item: item[1], reverse=True):
        print(f" - {store}: {fmt_currency(amount)}")


def print_pairs(summary: PurchaseSummary) -> None:
    if not summary.pair_totals:
        return
    print("\nДетализация по складу и контрагенту:")
    for (store, supplier), amount in sorted(summary.pair_totals.items(), key=lambda item: item[1], reverse=True):
        print(f" - {store} | {supplier}: {fmt_currency(amount)}")


def main() -> None:
    args = parse_args()
    transaction_types = args.transaction_types or DEFAULT_TRANSACTION_TYPES
    transaction_codes = args.transaction_codes or DEFAULT_TRANSACTION_CODES

    summary = asyncio.run(
        get_purchase_summary(
            date_from=args.date_from,
            date_to=args.date_to,
            transaction_types=transaction_types,
            transaction_codes=transaction_codes,
            store_filter=args.store,
            include_all_accounts=args.include_all_accounts,
            timeout=args.timeout,
        )
    )

    if not summary.rows_count:
        print("Данные отсутствуют по заданным условиям")
        return

    print(f"Всего строк: {summary.rows_count}")
    print(f"Суммарный приход: {fmt_currency(summary.total_amount)}")

    if not args.no_supplier_summary:
        print_suppliers(summary)
    if not args.no_store_summary:
        print_stores(summary)
    if args.list:
        print_pairs(summary)


if __name__ == "__main__":
    main()
