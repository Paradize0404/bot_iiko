
# ────────────── Импорт библиотек и общих функций ──────────────
import logging
from typing import Any, Awaitable, Callable, Iterable
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from states import RegisterStates
from keyboards.main_keyboard import main_menu_keyboard
from services.employees import fetch_employees
from services.position_sheet_sync import sync_positions_sheet
from db.employees_db import async_session, Employee
from db.nomenclature_db import fetch_nomenclature, sync_nomenclature, init_db, sync_store_balances
from db.group_db import init_groups_table, fetch_groups, sync_groups
from utils.telegram_helpers import safe_send_error, tidy_response
from db.stores_db import (
    init_stores_table,
    fetch_stores,
    sync_stores,
)
from fin_tab.fin_tab_employees_db import async_session as ft_async_session, FinTabEmployee
from sqlalchemy import select
from db.sprav_db import sync_all_references
from db.supplier_db import sync_suppliers
from db.accounts_data import sync_accounts, async_session, Account
from fin_tab.setup_accounts_sheet import main as sync_accounts_sheet
from fin_tab.sync_accounts_incoming import sync_incoming_service_accounts
from services.db_queries import DBQueries
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from scripts.fill_fot_sheet import main as fill_fot_sheet_main
logging.basicConfig(level=logging.INFO)

# ────────────── Логгер и роутер для aiogram ──────────────
router = Router()


# ────────────── Общий раннер для загрузочных команд ──────────────
async def _run_loader(
    target: Message,
    loader: Callable[[], Awaitable[Any]],
    success: str | Callable[[Any], str],
    *,
    edit: bool = False,
):
    try:
        # Показываем пользователю, что процесс запущен (особенно при долгих операциях)
        if edit:
            await target.edit_text("⏳ Идёт загрузка...")
        else:
            await target.answer("⏳ Идёт загрузка...")

        result = await loader()
        text = success(result) if callable(success) else success
        if edit:
            await target.edit_text(text)
        else:
            await target.answer(text)
    except Exception as exc:  # noqa: BLE001
        if edit:
            await target.edit_text(f"❌ Ошибка: {exc}")
        else:
            await safe_send_error(target, exc)


# ────────────── Общие загрузчики ──────────────
async def _load_staff():
    positions_count = await sync_positions_sheet()
    employees = await fetch_employees()
    return positions_count, employees


async def _load_products():
    await init_db()
    data = await fetch_nomenclature()
    await sync_nomenclature(data)
    await sync_store_balances(data)


async def _load_groups():
    await init_groups_table()
    data = await fetch_groups()
    await sync_groups(data)


async def _load_stores():
    await init_stores_table()
    data = await fetch_stores()
    await sync_stores(data)


async def _load_references():
    await sync_all_references()


async def _load_suppliers():
    await sync_suppliers()


async def _load_accounts():
    # Снимок текущих счетов до синка, чтобы сопоставить старые имена с ID
    async with async_session() as session:
        prev_rows = await session.execute(
            select(Account.id, Account.name).where(
                Account.deleted.is_(False),
                Account.extra["type"].astext == "EXPENSES",
            )
        )
        prev_accounts = [(r[0], r[1]) for r in prev_rows.fetchall() if r[0] and r[1]]

    # 1) подтягиваем свежие счета из iiko в БД
    await sync_accounts()
    # 2) обновляем выпадающий список счетов из iiko в таблице
    # 3) обновляем таблицу категориями FinTablo, сохраняя привязки по ID
    await sync_accounts_sheet(prev_accounts)
    # 4) отправляем суммы INCOMING_SERVICE по маппингу в FinTablo
    await sync_incoming_service_accounts()


async def _load_fot_sheet():
    await fill_fot_sheet_main()


async def _run_fin_tab_sync():
    # Ручной запуск только нашей FinTablo синхронизации INCOMING_SERVICE
    await sync_incoming_service_accounts()


## ────────────── Inline-меню и обработчики команд администратора ──────────────
@router.message(F.text == "Команды")
async def show_commands_list(message: types.Message):
    """Показывает список всех доступных команд администратора"""
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 Загрузить сотрудников", callback_data="cmd:load_staff")],
            [InlineKeyboardButton(text="📦 Загрузить номенклатуру", callback_data="cmd:load_products")],
            [InlineKeyboardButton(text="📁 Загрузить группы", callback_data="cmd:load_groups")],
            [InlineKeyboardButton(text="🏪 Загрузить склады", callback_data="cmd:load_stores")],
            [InlineKeyboardButton(text="📚 Загрузить справочники", callback_data="cmd:load_references")],
            [InlineKeyboardButton(text="🚚 Загрузить поставщиков", callback_data="cmd:load_suppliers")],
            [InlineKeyboardButton(text="💳 Загрузить счета", callback_data="cmd:load_accounts")],
            [InlineKeyboardButton(text="🧾 Обновить ФОТ", callback_data="cmd:fill_fot")],
            [InlineKeyboardButton(text="🔄 FinTablo sync (ручн.)", callback_data="cmd:fin_tab_sync")],
            [InlineKeyboardButton(text="🆔 Показать FinTablo ID", callback_data="cmd:show_fintablo_ids")],
        ]
    )
    await message.answer("Выберите команду для выполнения:", reply_markup=keyboard)


@router.callback_query(F.data == "cmd:load_staff")
async def callback_load_staff(callback: types.CallbackQuery):
    """Загрузка сотрудников"""
    await callback.answer()
    await _run_loader(
        callback.message,
        _load_staff,
        lambda data: (
            f"✅ Должности в таблице обновлены ({data[0]} строк). "
            f"👥 Загружено сотрудников: {len(data[1])}"
        ),
        edit=True,
    )


async def _fetch_fintablo_ids() -> list[tuple[str, int]]:
    async with ft_async_session() as session:
        result = await session.execute(
            select(FinTabEmployee.name, FinTabEmployee.id).order_by(FinTabEmployee.name)
        )
        return [(row[0], row[1]) for row in result.all()]


def _chunk_messages(lines: Iterable[str], limit: int = 3800) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        if current_len + len(line) + 1 > limit and current:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


@router.callback_query(F.data == "cmd:show_fintablo_ids")
async def callback_show_fintablo_ids(callback: types.CallbackQuery):
    """Показать список сотрудников с FinTablo ID."""
    await callback.answer()
    try:
        pairs = await _fetch_fintablo_ids()
        if not pairs:
            await callback.message.edit_text("ℹ️ Нет данных FinTablo ID (таблица пуста)")
            return

        lines = [f"{name} — <code>{emp_id}</code>" for name, emp_id in pairs]
        chunks = _chunk_messages(lines)

        await callback.message.edit_text("🆔 FinTablo ID загружены, отправляю список...")
        for chunk in chunks:
            await callback.message.answer(chunk)
    except Exception as exc:  # noqa: BLE001
        await safe_send_error(callback.message, exc)


@router.callback_query(F.data == "cmd:load_products")
async def callback_load_products(callback: types.CallbackQuery):
    """Загрузка номенклатуры и балансов"""
    await callback.answer()
    await _run_loader(
        callback.message,
        _load_products,
        "✅ Номенклатура и балансы обновлены",
        edit=True,
    )


@router.callback_query(F.data == "cmd:load_groups")
async def callback_load_groups(callback: types.CallbackQuery):
    """Загрузка групп номенклатуры"""
    await callback.answer()
    await _run_loader(
        callback.message,
        _load_groups,
        "✅ Группы номенклатуры обновлены",
        edit=True,
    )


@router.callback_query(F.data == "cmd:load_stores")
async def callback_load_stores(callback: types.CallbackQuery):
    """Загрузка складов"""
    await callback.answer()
    await _run_loader(
        callback.message,
        _load_stores,
        "✅ Склады обновлены",
        edit=True,
    )


@router.callback_query(F.data == "cmd:load_references")
async def callback_load_references(callback: types.CallbackQuery):
    """Загрузка справочников"""
    await callback.answer()
    await _run_loader(
        callback.message,
        _load_references,
        "✅ Все справочники синхронизированы",
        edit=True,
    )


@router.callback_query(F.data == "cmd:load_suppliers")
async def callback_load_suppliers(callback: types.CallbackQuery):
    """Загрузка поставщиков"""
    await callback.answer()
    await _run_loader(
        callback.message,
        _load_suppliers,
        "✅ Поставщики успешно синхронизированы",
        edit=True,
    )


@router.callback_query(F.data == "cmd:load_accounts")
async def callback_load_accounts(callback: types.CallbackQuery):
    """Загрузка счетов"""
    await callback.answer()
    await _run_loader(
        callback.message,
        _load_accounts,
        "✅ Счета успешно загружены в таблицу accounts",
        edit=True,
    )


@router.callback_query(F.data == "cmd:fin_tab_sync")
async def callback_fin_tab_sync(callback: types.CallbackQuery):
    """Ручной запуск FinTablo синхронизации INCOMING_SERVICE"""
    await callback.answer()
    await _run_loader(
        callback.message,
        _run_fin_tab_sync,
        "✅ FinTablo синхронизация выполнена",
        edit=True,
    )


@router.callback_query(F.data == "cmd:fill_fot")
async def callback_fill_fot(callback: types.CallbackQuery):
    """Ручной запуск заполнения ФОТ-листа"""
    await callback.answer()
    await _run_loader(
        callback.message,
        _load_fot_sheet,
        "✅ ФОТ-лист обновлён",
        edit=True,
    )


# ──────────────────────────────── /start ────────────────────────────────
@router.message(F.text.startswith("/start"))
async def start(message: Message, state: FSMContext):
    logging.info(f"📨 /start от {message.from_user.id}")
    msg = await message.answer("Как тебя зовут (Напиши свою фамилию)?")
    await state.set_state(RegisterStates.waiting_for_name)
    await state.update_data(question_msg_id=msg.message_id)


# ─────────────────────────── регистрация: фамилия ───────────────────────────
@router.message(RegisterStates.waiting_for_name)
async def get_name(message: Message, state: FSMContext):
    logging.info(f"👤 Фамилия получена от {message.from_user.id}: {message.text}")

    last_name = message.text.strip()
    data = await state.get_data()
    msg_id = data.get("question_msg_id")

    async with async_session() as session:
        # ищем сотрудника по фамилии
        result = await session.execute(
            Employee.__table__.select().where(Employee.last_name == last_name)
        )
        row = result.fetchone()

        if row:  # 🎉 Пользователь найден
            employee = await session.get(Employee, row[0])
            employee.telegram_id = str(message.from_user.id)
            await session.commit()

            greet_text = f"Привет, {employee.first_name} 👋"

            await tidy_response(
                message,
                greet_text,
                old_msg_id=msg_id,
            )

            await message.answer(
                "Вот главное меню:",
                reply_markup=main_menu_keyboard(message.from_user.id),
            )
            await state.clear()

        else:  # 🚫 Пользователь не найден
            warn_text = "🚫 Доступ запрещён. Сотрудник не найден."

            await tidy_response(
                message,
                warn_text,
                old_msg_id=msg_id,
                reply_markup=types.ReplyKeyboardRemove(),
            )




# ─────────────────────────────── /load_staff ───────────────────────────────
@router.message(F.text == "/load_staff")
async def load_staff(message: Message):
    await _run_loader(
        message,
        _load_staff,
        lambda data: (
            f"✅ Должности в таблице обновлены ({data[0]} строк). "
            f"👥 Загружено сотрудников: {len(data[1])}"
        ),
    )


# ───────────────────────────── /load_products ─────────────────────────────
@router.message(F.text == "/load_products")
async def load_products(message: Message):
    await _run_loader(
        message,
        _load_products,
        "✅ Номенклатура и балансы обновлены",
    )


# ─────────────────────────────── /load_groups ───────────────────────────────
@router.message(F.text == "/load_groups")
async def load_groups(message: Message):
    await _run_loader(
        message,
        _load_groups,
        "✅ Группы номенклатуры обновлены",
    )

@router.message(F.text == "/load_stores")
async def load_stores(message: types.Message):
    await _run_loader(
        message,
        _load_stores,
        "✅ Склады обновлены",
    )


@router.message(F.text == "/load_references")
async def load_references(message: Message):
    await _run_loader(
        message,
        _load_references,
        "✅ Все справочники синхронизированы",
    )


@router.message(F.text == "/load_supplyers")
async def sync_suppliers_command(message: Message):
    await _run_loader(
        message,
        _load_suppliers,
        "🔄 Поставщики успешно синхронизированы.",
    )


@router.message(F.text == "/load_accounts")
async def load_accounts_command(message: Message):
    await _run_loader(
        message,
        _load_accounts,
        "✅ Счета успешно загружены в таблицу accounts.",
    )


@router.message(Command("cancel"))
async def cancel_any_state(message: Message, state: FSMContext):
    """Глобальная команда отмены — сбрасывает FSM и возвращает главное меню."""
    await state.clear()
    await message.answer(
        "❌ Действие отменено. Главное меню:",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )