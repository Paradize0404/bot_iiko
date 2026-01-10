"""
Консольный тест для выгрузки закупок (сырьё/товар) через OLAP TRANSACTIONS.

Скрипт собирает данные по всем складам за заданный период, группирует их по складу
и наименованию номенклатуры и печатает агрегированные значения в консоль.

Пример запуска:

        python test_purchase_dump.py --from-date 2025-11-01 --to-date 2025-11-26 \
                --transaction-type INCOMING_INVOICE --transaction-type INTERNAL_ORDER_IN

Флаги:
    --from-date / --to-date — период в формате YYYY-MM-DD (по умолчанию последние 7 дней)
    --transaction-type     — тип(ы) транзакций OLAP TRANSACTIONS (можно повторять)
    --limit                — ограничить количество строк на склад (по умолчанию без ограничений)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
import httpx
import xml.etree.ElementTree as ET
from typing import Any, Iterable, Sequence

from iiko.iiko_auth import get_auth_token, get_base_url

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ────────────────────────────── Константы ────────────────────────────────
BASE_GROUP_DIMENSIONS = (
    "Account.Name",        # Склад / счёт
    "Product.TopParent",   # Группа 1-го уровня
    "Product.SecondParent",# Группа 2-го уровня
)

ITEM_DIMENSIONS = (
    "Product.Name",        # Элемент номенклатуры
    "Product.MeasureUnit", # Ед. изм.
)

CANDIDATE_FIELDS = {
    "store": ("Account.Name", "Store", "Счет", "Склад"),
    "item": (
        "Product.Name",
        "Product",
        "Item",
        "Элемент номенклатуры",
    ),
    "group_top": ("Product.TopParent", "Product.Group", "Группа", "Группа номенклатуры 1-го уровня"),
    "group_second": ("Product.SecondParent", "Группа 2-го уровня"),
    "unit": ("Product.MeasureUnit", "Unit", "MeasureUnit", "Единица измерения"),
    "type": (
        "Product.Type",
        "Тип элемента номенклатуры",
    ),
    "incoming_sum": ("Sum.Incoming", "SumIn", "Сумма прихода"),
    "outgoing_sum": ("Sum.Outgoing", "SumOut", "Сумма расхода"),
    "incoming_qty": (
        "Amount.In",
        "IncomingAmount",
        "Приход (кол-во)",
    ),
    "outgoing_qty": (
        "Amount.Out",
        "OutgoingAmount",
        "Расход (кол-во)",
    ),
}

AGGREGATE_SETS = [
    ["Sum.Incoming", "Sum.Outgoing", "Amount.In", "Amount.Out"],
    ["Sum.Incoming", "Amount.In"],
]

NUMBER_FORMAT = "{:,.2f}"

# Типы транзакций, которые появляются при включённой галочке
# «Коррекция себестоимости» в интерфейсе iiko OLAP.
COST_CORRECTION_TRANSACTION_TYPES = ("STORE_COST_CORRECTION",)
DEFAULT_CORRECTION_ACCOUNT_NAMES = ("Коррекция отрицательных остатков на складе",)
CORRECTION_GROUP_LABEL_DEFAULT = "Коррекция себестоимости"


# ────────────────────────────── Парсинг XML ───────────────────────────────
def _auto_cast(text: str | None) -> Any:
    if text is None:
        return None
    try:
        return int(text)
    except Exception:
        try:
            return Decimal(text)
        except Exception:
            return text.strip() if text else None


def parse_xml_report(xml: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml)
    rows = []
    for row in root.findall("./r"):
        rows.append({child.tag: _auto_cast(child.text) for child in row})
    return rows


# ────────────────────────────── Утилиты ───────────────────────────────────
def resolve_field(rows: list[dict[str, Any]], candidates: Iterable[str]) -> str | None:
    keys = {key for row in rows for key in row.keys()}
    for candidate in candidates:
        if candidate in keys:
            return candidate
    return None


def to_float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except Exception:
        return 0.0


def fmt_qty(value: float | None) -> str:
    if value is None:
        return "—"
    if value == 0:
        return "0"
    if abs(value) >= 100:
        return f"{value:,.0f}"
    return f"{value:.3f}"


def human_date(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")


def json_serializer(obj: Any):
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return str(obj)


# ────────────────────────────── Запрос к OLAP ─────────────────────────────
async def fetch_transactions(
    date_from: str,
    date_to: str,
    transaction_types: list[str],
    timeout: float,
    group_dimensions: tuple[str, ...],
) -> tuple[list[dict[str, Any]], list[str]]:
    token = await get_auth_token()
    base_url = get_base_url()

    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, verify=False) as client:
        last_error: str | None = None
        for agr_list in AGGREGATE_SETS:
            params: list[tuple[str, str]] = [
                ("key", token),
                ("report", "TRANSACTIONS"),
                ("from", date_from),
                ("to", date_to),
            ]
            for dimension in group_dimensions:
                params.append(("groupRow", dimension))
            for agr in agr_list:
                params.append(("agr", agr))
            for trx in transaction_types:
                params.append(("TransactionType", trx))

            logger.info(
                "⏳ Запрашиваем TRANSACTIONS: %s → %s (метрики: %s)",
                date_from,
                date_to,
                ", ".join(agr_list),
            )
            response = await client.get("/resto/api/reports/olap", params=params)
            if response.status_code == 200:
                rows = parse_response(response)
                logger.info("✅ Получено %d строк", len(rows))
                return rows, agr_list

            last_error = f"{response.status_code}: {response.text[:400]}"
            logger.warning("⚠️ Ошибка OLAP (%s)", last_error)

        raise RuntimeError(f"Не удалось получить TRANSACTIONS: {last_error}")


def parse_response(response: httpx.Response) -> list[dict[str, Any]]:
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("application/json"):
        payload = response.json()
        return payload.get("data") or payload.get("rows") or []
    if content_type.startswith("application/xml") or content_type.startswith("text/xml"):
        return parse_xml_report(response.text)
    raise RuntimeError(f"Неизвестный формат ответа: {content_type}\n{response.text[:400]}")


async def fetch_store_names(store_type: str = "STORE", timeout: float = 60.0) -> set[str]:
    token = await get_auth_token()
    base_url = get_base_url()
    params = {"key": token, "revisionFrom": -1}
    async with httpx.AsyncClient(base_url=base_url, timeout=timeout, verify=False) as client:
        response = await client.get("/resto/api/corporation/stores", params=params)
        response.raise_for_status()
    root = ET.fromstring(response.text)
    names: set[str] = set()
    for item in root.findall("corporateItemDto"):
        type_value = (item.findtext("type") or "").upper()
        name = (item.findtext("name") or "").strip()
        if not name:
            continue
        if store_type and type_value != store_type.upper():
            continue
        names.add(name)
    return names


# ────────────────────────────── Отчёт в консоль ────────────────────────────
def render_report(rows: list[dict[str, Any]], limit: int | None, group_only: bool) -> None:
    if not rows:
        print("Нет данных по заданному периоду.")
        return

    field_map = {
        name: resolve_field(rows, candidates)
        for name, candidates in CANDIDATE_FIELDS.items()
    }

    store_field = field_map["store"] or "Account.Name"
    item_field = None if group_only else field_map["item"] or "Product.Name"
    unit_field = None if group_only else field_map["unit"] or "Product.MeasureUnit"
    type_field = field_map["type"] if not group_only else None
    group_top_field = field_map.get("group_top")
    group_second_field = field_map.get("group_second")
    incoming_sum_field = field_map["incoming_sum"] or "Sum.Incoming"
    outgoing_sum_field = field_map["outgoing_sum"] or "Sum.Outgoing"
    incoming_qty_field = field_map["incoming_qty"]
    outgoing_qty_field = field_map["outgoing_qty"]

    available_keys = sorted({key for row in rows for key in row.keys()})
    print("Доступные колонки:")
    print(", ".join(available_keys))

    grouped = defaultdict(list)
    grand_in, grand_out = 0.0, 0.0

    for row in rows:
        store = str(row.get(store_field) or "(Без склада)")
        item = str(row.get(item_field) or "(Группа)") if item_field else "(Группа)"
        unit = str(row.get(unit_field) or "") if unit_field else ""
        item_type = str(row.get(type_field) or "") if type_field else ""
        group_top = str(row.get(group_top_field) or "") if group_top_field else ""
        group_second = str(row.get(group_second_field) or "") if group_second_field else ""
        incoming_sum = to_float(row.get(incoming_sum_field))
        outgoing_sum = to_float(row.get(outgoing_sum_field))
        incoming_qty = (
            to_float(row.get(incoming_qty_field)) if incoming_qty_field else None
        )
        outgoing_qty = (
            to_float(row.get(outgoing_qty_field)) if outgoing_qty_field else None
        )

        grouped[store].append(
            {
                "item": item,
                "unit": unit,
                "type": item_type,
                "group_top": group_top,
                "group_second": group_second,
                "incoming_sum": incoming_sum,
                "outgoing_sum": outgoing_sum,
                "incoming_qty": incoming_qty,
                "outgoing_qty": outgoing_qty,
            }
        )
        grand_in += incoming_sum
        grand_out += outgoing_sum

    for store_name in sorted(grouped.keys()):
        items = grouped[store_name]
        items.sort(key=lambda x: x["incoming_sum"], reverse=True)
        print("\n" + "=" * 120)
        print(f"Склад: {store_name} (позиций: {len(items)})")
        print("=" * 120)
        if group_only:
            header = (
                f"{'Группа 1':<25} {'Группа 2':<25} "
                f"{'Приход, кол-во':>15} {'Приход ₽':>14}"
            )
        else:
            header = (
                f"{'Группа 1':<18} {'Группа 2':<18} {'Номенклатура':<40} {'Тип':<12} "
                f"{'Приход, кол-во':>15} {'Ед.':<6} {'Приход ₽':>14}"
            )
        print(header)
        print("-" * len(header))

        rows_to_show = items if limit is None else items[:limit]
        for item in rows_to_show:
            if group_only:
                print(
                    f"{item['group_top'][:25]:<25} "
                    f"{item['group_second'][:25]:<25} "
                    f"{fmt_qty(item['incoming_qty']):>15} "
                    f"{NUMBER_FORMAT.format(item['incoming_sum']):>14}"
                )
            else:
                print(
                    f"{item['group_top'][:18]:<18} "
                    f"{item['group_second'][:18]:<18} "
                    f"{item['item'][:40]:<40} "
                    f"{item['type'][:12]:<12} "
                    f"{fmt_qty(item['incoming_qty']):>15} "
                    f"{item['unit']:<6} "
                    f"{NUMBER_FORMAT.format(item['incoming_sum']):>14}"
                )

        store_in = sum(x["incoming_sum"] for x in items)
        store_out = sum(x["outgoing_sum"] for x in items)
        print("-" * len(header))
        print(
            f"ИТОГО по складу: приход {NUMBER_FORMAT.format(store_in)} ₽ | "
            f"расход {NUMBER_FORMAT.format(store_out)} ₽"
        )
        if limit is not None and len(items) > limit:
            print(f"… показаны первые {limit} строк (всего {len(items)})")

    print("\n" + "=" * 120)
    print(
        f"ОБЩИЙ ИТОГ: приход {NUMBER_FORMAT.format(grand_in)} ₽ | "
        f"расход {NUMBER_FORMAT.format(grand_out)} ₽"
    )
    print("=" * 120)


def filter_rows_by_store(
    rows: list[dict[str, Any]],
    store_whitelist: Sequence[str] | set[str] | None,
) -> list[dict[str, Any]]:
    if not rows or not store_whitelist:
        return rows
    store_field = resolve_field(rows, CANDIDATE_FIELDS["store"]) or "Account.Name"
    allowed = {str(name).strip() for name in store_whitelist}
    filtered = [row for row in rows if str(row.get(store_field) or "").strip() in allowed]
    logger.info("После фильтрации по складам осталось %d из %d строк", len(filtered), len(rows))
    return filtered


def filter_rows_by_product_top(
    rows: list[dict[str, Any]],
    allowed_groups: Sequence[str] | set[str] | None,
) -> list[dict[str, Any]]:
    if not rows or not allowed_groups:
        return rows
    group_field = resolve_field(rows, CANDIDATE_FIELDS.get("group_top", ())) or "Product.TopParent"
    allowed = {str(name).strip() for name in allowed_groups}
    filtered = [row for row in rows if str(row.get(group_field) or "").strip() in allowed]
    logger.info(
        "После фильтрации по группам %s осталось %d из %d строк",
        ", ".join(sorted(allowed)),
        len(filtered),
        len(rows),
    )
    return filtered


def relabel_correction_rows(
    rows: list[dict[str, Any]],
    correction_accounts: set[str],
    target_store_name: str | None,
    label: str,
) -> list[dict[str, Any]]:
    if not rows or not correction_accounts:
        return []

    store_field = resolve_field(rows, CANDIDATE_FIELDS["store"]) or "Account.Name"
    group_top_field = resolve_field(rows, CANDIDATE_FIELDS.get("group_top", ())) or "Product.TopParent"
    group_second_field = (
        resolve_field(rows, CANDIDATE_FIELDS.get("group_second", ())) or "Product.SecondParent"
    )
    safe_label = label or CORRECTION_GROUP_LABEL_DEFAULT
    processed: list[dict[str, Any]] = []

    for row in rows:
        account_name = str(row.get(store_field) or "").strip()
        if account_name not in correction_accounts:
            continue
        row["_is_cost_correction"] = True
        row.setdefault("_original_account_name", account_name)
        if target_store_name:
            row[store_field] = target_store_name
        original_top = row.get(group_top_field)
        original_second = row.get(group_second_field)
        if original_top is not None:
            row.setdefault("_original_product_top", original_top)
        if original_second is not None:
            row.setdefault("_original_product_second", original_second)
        row[group_top_field] = safe_label
        row[group_second_field] = safe_label
        processed.append(row)

    return processed


def print_total_sum_and_rows(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("ОБЩАЯ СУММА ПРИХОДА: 0.00 ₽")
        print("[]")
        return

    field = resolve_field(rows, CANDIDATE_FIELDS["incoming_sum"]) or "Sum.Incoming"
    total = sum(to_float(row.get(field)) for row in rows)
    print(f"ОБЩАЯ СУММА ПРИХОДА: {NUMBER_FORMAT.format(total)} ₽")
    print(json.dumps(rows, ensure_ascii=False, indent=2, default=json_serializer))


# ────────────────────────────── CLI ───────────────────────────────────────
def parse_args() -> argparse.Namespace:
    today = datetime.now().date()
    default_start = today - timedelta(days=7)

    parser = argparse.ArgumentParser(description="OLAP TRANSACTIONS dump по складам")
    parser.add_argument(
        "--from-date",
        dest="date_from",
        default=default_start.strftime("%Y-%m-%d"),
        help="Дата начала периода (YYYY-MM-DD). По умолчанию 7 дней назад.",
    )
    parser.add_argument(
        "--to-date",
        dest="date_to",
        default=today.strftime("%Y-%m-%d"),
        help="Дата конца периода (YYYY-MM-DD). По умолчанию сегодня.",
    )
    parser.add_argument(
        "--transaction-type",
        dest="transaction_types",
        action="append",
        default=None,
        help="Фильтр по типу транзакции (можно указывать несколько).",
    )
    parser.add_argument(
        "--include-cost-correction",
        action="store_true",
        help="Включить строки коррекции себестоимости (корректировки остатков).",
    )
    parser.add_argument(
        "--correction-group-label",
        default=CORRECTION_GROUP_LABEL_DEFAULT,
        help="Какой заголовок присвоить строкам коррекции себестоимости (по умолчанию 'Коррекция себестоимости').",
    )
    parser.add_argument(
        "--correction-account",
        action="append",
        default=[],
        help="Добавить своё название счёта/склада, где лежат строки коррекции себестоимости.",
    )
    parser.add_argument(
        "--group-only",
        action="store_true",
        help="Группировать только по складам и группам номенклатуры (без конкретных позиций)",
    )
    parser.add_argument(
        "--store",
        action="append",
        default=[],
        help="Оставить в отчёте только указанные склады (название как в iiko). Можно повторять.",
    )
    parser.add_argument(
        "--product-top",
        dest="product_top",
        action="append",
        default=[],
        help="Оставить только указанные группы верхнего уровня (Product.TopParent).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Ограничить количество строк на склад (по умолчанию выводим все).",
    )
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Вывести сырые строки из OLAP перед форматированием.",
    )
    parser.add_argument(
        "--raw-limit",
        type=int,
        default=10,
        help="Сколько сырых строк печатать (по умолчанию 10).",
    )
    parser.add_argument(
        "--raw-only",
        action="store_true",
        help="Вывести только общую сумму прихода и сырые строки (без форматированного отчёта).",
    )
    parser.add_argument(
        "--include-all-accounts",
        action="store_true",
        help="Не фильтровать по списку складов из справочника (по умолчанию берём только настоящие склады).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="HTTP timeout в секундах для запроса OLAP.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()

    if args.raw_only:
        logging.getLogger().setLevel(logging.WARNING)
        logger.setLevel(logging.WARNING)

    date_from = human_date(args.date_from)
    date_to = human_date(args.date_to)

    raw_transaction_types = args.transaction_types or []
    transaction_types = [trx.strip() for trx in raw_transaction_types if trx and trx.strip()]
    if not transaction_types:
        transaction_types = ["INCOMING_INVOICE"]

    if args.include_cost_correction:
        filtered_types = [
            trx for trx in transaction_types if trx not in COST_CORRECTION_TRANSACTION_TYPES
        ]
        if len(filtered_types) != len(transaction_types):
            logger.info(
                "Исключаем типы коррекции из основного списка, чтобы не дублировать данные",
            )
        transaction_types = filtered_types

    logger.info(
        "Формируем выгрузку закупок: %s → %s | типы %s",
        date_from,
        date_to,
        ", ".join(transaction_types) if transaction_types else "(только коррекция)",
    )

    group_dimensions = BASE_GROUP_DIMENSIONS
    if not args.group_only:
        group_dimensions = BASE_GROUP_DIMENSIONS + ITEM_DIMENSIONS

    requested_stores = {name.strip() for name in args.store if name.strip()}
    store_whitelist: set[str] | None = None
    base_store_names: set[str] | None = None
    if not args.include_all_accounts:
        try:
            base_store_names = await fetch_store_names()
            logger.info("Фильтруем по %d складам", len(base_store_names))
        except Exception as exc:
            logger.warning(
                "Не удалось загрузить справочник складов (%s). Используем все счета.",
                exc,
            )
    if requested_stores:
        store_whitelist = requested_stores
        if base_store_names:
            intersection = store_whitelist & base_store_names
            if not intersection:
                logger.warning("Указанные склады не найдены в списке STORE, используем как есть")
            else:
                store_whitelist = intersection
    elif base_store_names:
        store_whitelist = base_store_names

    rows: list[dict[str, Any]] = []
    agrs: list[str] | None = None
    if transaction_types:
        rows, agrs = await fetch_transactions(
            date_from=date_from,
            date_to=date_to,
            transaction_types=transaction_types,
            timeout=args.timeout,
            group_dimensions=group_dimensions,
        )
        logger.info("Использованные метрики: %s", ", ".join(agrs))
    else:
        logger.info("Основной запрос пропущен: все типы относятся к коррекции себестоимости")

    if store_whitelist:
        rows = filter_rows_by_store(rows, store_whitelist)

    product_top_filter = {name.strip() for name in args.product_top if name.strip()}
    correction_rows: list[dict[str, Any]] = []
    correction_accounts = {
        name.strip()
        for name in DEFAULT_CORRECTION_ACCOUNT_NAMES
        if name and name.strip()
    }
    user_accounts = {name.strip() for name in args.correction_account if name and name.strip()}
    correction_accounts.update(user_accounts)
    correction_label = args.correction_group_label or CORRECTION_GROUP_LABEL_DEFAULT
    correction_store_name = None
    if requested_stores and len(requested_stores) == 1:
        correction_store_name = next(iter(requested_stores))

    if args.include_cost_correction:
        if product_top_filter:
            product_top_filter.add(correction_label)
        correction_rows, corr_agrs = await fetch_transactions(
            date_from=date_from,
            date_to=date_to,
            transaction_types=list(COST_CORRECTION_TRANSACTION_TYPES),
            timeout=args.timeout,
            group_dimensions=group_dimensions,
        )
        logger.info(
            "Коррекция себестоимости: %d строк (метрики: %s)",
            len(correction_rows),
            ", ".join(corr_agrs),
        )
        if requested_stores:
            correction_rows = filter_rows_by_store(correction_rows, requested_stores)
        correction_rows = relabel_correction_rows(
            correction_rows,
            correction_accounts,
            correction_store_name,
            correction_label,
        )
        if correction_rows:
            rows.extend(correction_rows)
            incoming_field = (
                resolve_field(correction_rows, CANDIDATE_FIELDS["incoming_sum"]) or "Sum.Incoming"
            )
            correction_sum = sum(to_float(row.get(incoming_field)) for row in correction_rows)
            logger.info(
                "Добавлено строк коррекции: %d (сумма прихода %.2f) как '%s' для счетов: %s",
                len(correction_rows),
                correction_sum,
                correction_label,
                ", ".join(sorted(correction_accounts)) or "(не указано)",
            )
        else:
            logger.warning(
                "Строки коррекции не найдены: убедитесь, что название счёта совпадает (%s)",
                ", ".join(sorted(correction_accounts)) or "(не указано)",
            )

    if product_top_filter:
        rows = filter_rows_by_product_top(rows, product_top_filter)

    if args.raw_only:
        print_total_sum_and_rows(rows)
        return

    if args.raw:
        dump_raw_rows(rows, args.raw_limit)

    render_report(rows, args.limit, group_only=args.group_only)


def dump_raw_rows(rows: list[dict[str, Any]], limit: int | None) -> None:
    if not rows:
        print("Сырых данных нет (ответ пустой).")
        return

    limit = limit if limit is not None and limit > 0 else len(rows)
    subset = rows[:limit]

    print("\nRAW строки из OLAP (первые %d):" % len(subset))
    print(json.dumps(subset, ensure_ascii=False, indent=2, default=json_serializer))
    if len(rows) > len(subset):
        print(f"… всего строк: {len(rows)}")


if __name__ == "__main__":
    asyncio.run(main())
