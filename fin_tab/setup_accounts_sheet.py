"""Создаёт/обновляет лист "настройка счетов" и заполняет колонку A названиями статей
FinTablo, которые *не* ставятся автоматически нашими скриптами.

Колонки:
- A: "Счета в финтабло" — categoryName из FinTablo, только те, что не автозаполняются
- B: "Счета в айко" — выпадающий список счетов iiko
- C (скрытый): ID категории FinTablo
- D (скрытый): ID счета iiko для привязки/переименований

Запуск:
    python -m fin_tab.setup_accounts_sheet
"""
import asyncio
import logging
from typing import Dict, List, Tuple

from dotenv import load_dotenv

from fin_tab.client import FinTabloClient
from services.gsheets_client import GoogleSheetsClient
from db.accounts_data import async_session, Account
from sqlalchemy import select

SHEET_TITLE = "настройка счетов"
HEADERS = [
    "Счета в финтабло",
    "Счета в айко",
    "FinTablo ID (hidden)",
    "iiko Account ID (hidden)",
]

# Статьи, которые заполняем автоскриптами (не кладём в лист для ручного маппинга)
AUTO_CATEGORY_IDS = {
    # Выручка / себестоимость / списания
    27314,  # Кухня
    27315,  # Бар
    27316,  # Приложение
    27317,  # Яндекс
    27318,  # Производство
    27319,  # Сырьевая себестоимость
    27321,  # Списания продуктов
    # ТМЦ / хозтовары
    27327,  # Покупка ТМЦ
    27328,  # Стекло/Посуда
    27325,  # Упаковка
    27324,  # Уборочный инвентарь/Химия
    27326,  # Расходные материалы
}


def _ensure_sheet_and_headers(client: GoogleSheetsClient) -> None:
    """Создаёт лист при отсутствии и проставляет заголовки A1:B1."""
    service = client.service
    meta = service.spreadsheets().get(spreadsheetId=client.spreadsheet_id).execute()
    sheets = meta.get("sheets", [])
    titles = {s.get("properties", {}).get("title") for s in sheets}

    if SHEET_TITLE not in titles:
        service.spreadsheets().batchUpdate(
            spreadsheetId=client.spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": SHEET_TITLE}}}]},
        ).execute()
        logging.info("Добавлен лист %s", SHEET_TITLE)

    client.write_range(f"'{SHEET_TITLE}'!A1:D1", [HEADERS])


def _write_accounts(
    client: GoogleSheetsClient,
    rows: List[Tuple[str, int]],
    account_names_by_id: Dict[str, str] | None = None,
    name_to_id: Dict[str, str] | None = None,
) -> None:
    """Записывает имя/ID и сохраняет выбранные счета из колонки B по ID.

    При наличии account_names_by_id обновляет отображаемое название выбранного счёта,
    если он был переименован (используем ID в тексте "Название - id").
    """
    # Сохраняем существующие выборы B и ID iiko (колонка D), чтобы не сдвигались при обновлении.
    existing = client.read_range(f"'{SHEET_TITLE}'!A2:D")
    preserved: dict[int, Tuple[str, str]] = {}
    for r in existing:
        if len(r) >= 3:
            try:
                cat_id = int(r[2])
            except ValueError:
                continue
            selected_name = r[1] if len(r) >= 2 else ""
            selected_acc_id = r[3] if len(r) >= 4 else ""
            preserved[cat_id] = (selected_name, selected_acc_id)

    # Чистим старые данные
    client.clear_range(f"'{SHEET_TITLE}'!A2:D")

    if not rows:
        return

    resolved_name_to_id: Dict[str, str] = {}
    if name_to_id is not None:
        resolved_name_to_id = dict(name_to_id)
    elif account_names_by_id:
        resolved_name_to_id = {v: k for k, v in account_names_by_id.items()}

    values = []
    for name, cat_id in rows:
        selected_name, selected_acc_id = preserved.get(cat_id, ("", ""))

        # Если ID уже есть, обновляем имя из справочника
        if selected_acc_id and account_names_by_id and selected_acc_id in account_names_by_id:
            selected_name = account_names_by_id[selected_acc_id]

        # Если ID нет, но строка в формате "name - id"
        if not selected_acc_id and selected_name:
            parts = selected_name.rsplit(" - ", 1)
            if len(parts) == 2 and parts[1] in resolved_name_to_id.values():
                selected_acc_id = parts[1]
                selected_name = parts[0]

        # Если ID всё ещё нет, попробуем найти по имени
        if not selected_acc_id and selected_name and selected_name in resolved_name_to_id:
            selected_acc_id = resolved_name_to_id[selected_name]

        # Финальная строка для отображения: только имя (ID держим скрытым)
        display_value = selected_name
        if selected_acc_id and account_names_by_id and selected_acc_id in account_names_by_id:
            display_value = account_names_by_id[selected_acc_id]

        values.append([name, display_value, str(cat_id), selected_acc_id])

    client.write_range(f"'{SHEET_TITLE}'!A2", values)


def _clear_dropdown(client: GoogleSheetsClient, sheet_id: int, total_rows: int) -> None:
    """Сбрасывает data validation в колонке B на всём листе."""
    if total_rows <= 1:
        return

    req = {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,  # со 2-й строки
                "endRowIndex": total_rows,
                "startColumnIndex": 1,  # колонка B
                "endColumnIndex": 2,
            },
            "rule": None,
        }
    }

    client.service.spreadsheets().batchUpdate(
        spreadsheetId=client.spreadsheet_id, body={"requests": [req]}
    ).execute()


def _hide_hidden_columns(client: GoogleSheetsClient, sheet_id: int) -> None:
    """Скрывает колонки C и D с ID."""
    req = {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": 2,  # C (0-based)
                "endIndex": 4,    # до D включительно
            },
            "properties": {"hiddenByUser": True},
            "fields": "hiddenByUser",
        }
    }

    client.service.spreadsheets().batchUpdate(
        spreadsheetId=client.spreadsheet_id, body={"requests": [req]}
    ).execute()


async def _fetch_iiko_expense_accounts() -> List[Tuple[str, str]]:
    """Берём из БД iiko только счета с extra.type == EXPENSES и не удалённые (id, name)."""
    async with async_session() as session:
        rows = await session.execute(
            select(Account.id, Account.name).where(
                Account.deleted.is_(False),
                Account.extra["type"].astext == "EXPENSES",
            ).order_by(Account.name)
        )
        return [(r[0], r[1]) for r in rows.fetchall() if r[0] and r[1]]


def _set_expense_dropdown(
    client: GoogleSheetsClient, sheet_id: int, values: List[str], row_count: int
) -> None:
    """Вешает в колонку B выпадающий список счетов iiko (type=EXPENSES) только на строки с данными."""
    if not values or row_count <= 0:
        return

    req = {
        "setDataValidation": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,  # со 2-й строки
                "endRowIndex": 1 + row_count,  # только заполненные строки
                "startColumnIndex": 1,  # колонка B
                "endColumnIndex": 2,
            },
            "rule": {
                "condition": {
                    "type": "ONE_OF_LIST",
                    "values": [{"userEnteredValue": v} for v in values],
                },
                "showCustomUi": True,
                "strict": False,
            },
        }
    }

    client.service.spreadsheets().batchUpdate(
        spreadsheetId=client.spreadsheet_id, body={"requests": [req]}
    ).execute()


async def _fetch_fintablo_accounts() -> List[Tuple[str, int]]:
    """Возвращает отсортированный список (name, id) категорий вне AUTO_CATEGORY_IDS."""
    async with FinTabloClient() as cli:
        categories = await cli.list_pnl_categories()

    rows = [
        (c.get("name"), int(c.get("id")))
        for c in categories
        if c.get("name") and c.get("id") and c.get("id") not in AUTO_CATEGORY_IDS
    ]
    rows = sorted(rows, key=lambda x: x[0])
    return rows


async def main(prev_accounts: List[Tuple[str, str]] | None = None) -> int:
    load_dotenv()
    client = GoogleSheetsClient()

    _ensure_sheet_and_headers(client)

    # Берём счета iiko (id, name) заранее, чтобы:
    # - сформировать выпадающий список
    # - обновить сохранённые выборы, если счёт переименовали
    iiko_accounts = await _fetch_iiko_expense_accounts()
    iiko_display = [name for acc_id, name in iiko_accounts]
    account_names_by_id = {acc_id: name for acc_id, name in iiko_accounts}

    # Карта имя -> id: объединяем новые имена и старые (до синка)
    name_to_id_combined: Dict[str, str] = {name: acc_id for acc_id, name in iiko_accounts}
    if prev_accounts:
        for acc_id, name in prev_accounts:
            if name and acc_id:
                name_to_id_combined.setdefault(name, acc_id)

    fintablo_rows = await _fetch_fintablo_accounts()
    _write_accounts(client, fintablo_rows, account_names_by_id, name_to_id_combined)

    sheet_id = client._get_sheet_id(SHEET_TITLE)  # внутренний helper клиента

    # Узнаём высоту листа, чтобы очистить валидацию на всей колонке B
    meta = client.service.spreadsheets().get(spreadsheetId=client.spreadsheet_id).execute()
    sheets = meta.get("sheets", [])
    row_count_total = 0
    for s in sheets:
        props = s.get("properties", {})
        if props.get("title") == SHEET_TITLE:
            row_count_total = props.get("gridProperties", {}).get("rowCount", 1000)
            break
    if not row_count_total:
        row_count_total = 1000

    _clear_dropdown(client, sheet_id, row_count_total)

    _set_expense_dropdown(client, sheet_id, iiko_display, len(fintablo_rows))

    # Скрываем колонки C и D с ID
    _hide_hidden_columns(client, sheet_id)

    logging.info("✅ В лист '%s' записано счетов: %d", SHEET_TITLE, len(fintablo_rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
