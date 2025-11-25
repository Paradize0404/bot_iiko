
# ────────────── Импорт библиотек и общих функций ──────────────
import logging
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from states import RegisterStates
from keyboards.main_keyboard import main_menu_keyboard
from services.employees import fetch_employees
from db.employees_db import async_session, Employee
from db.nomenclature_db import fetch_nomenclature, sync_nomenclature, init_db, sync_store_balances
from db.group_db import init_groups_table, fetch_groups, sync_groups
from utils.telegram_helpers import safe_send_error, tidy_response
from db.stores_db import (
    init_stores_table,
    fetch_stores,
    sync_stores,
)
from db.sprav_db import sync_all_references
from db.supplier_db import sync_suppliers
from db.accounts_data import sync_accounts
from services.db_queries import DBQueries
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
logging.basicConfig(level=logging.INFO)

# ────────────── Логгер и роутер для aiogram ──────────────
router = Router()


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
        ]
    )
    await message.answer("Выберите команду для выполнения:", reply_markup=keyboard)


@router.callback_query(F.data == "cmd:load_staff")
async def callback_load_staff(callback: types.CallbackQuery):
    """Загрузка сотрудников"""
    await callback.answer()
    employees = await fetch_employees()
    await callback.message.edit_text(f"✅ Загружено сотрудников: {len(employees)}")


@router.callback_query(F.data == "cmd:load_products")
async def callback_load_products(callback: types.CallbackQuery):
    """Загрузка номенклатуры и балансов"""
    await callback.answer()
    try:
        await init_db()
        data = await fetch_nomenclature()
        await sync_nomenclature(data)
        await sync_store_balances(data)
        await callback.message.edit_text("✅ Номенклатура и балансы обновлены")
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data == "cmd:load_groups")
async def callback_load_groups(callback: types.CallbackQuery):
    """Загрузка групп номенклатуры"""
    await callback.answer()
    try:
        await init_groups_table()
        data = await fetch_groups()
        await sync_groups(data)
        await callback.message.edit_text("✅ Группы номенклатуры обновлены")
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data == "cmd:load_stores")
async def callback_load_stores(callback: types.CallbackQuery):
    """Загрузка складов"""
    await callback.answer()
    try:
        await init_stores_table()
        data = await fetch_stores()
        await sync_stores(data)
        await callback.message.edit_text("✅ Склады обновлены")
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data == "cmd:load_references")
async def callback_load_references(callback: types.CallbackQuery):
    """Загрузка справочников"""
    await callback.answer()
    try:
        await sync_all_references()
        await callback.message.edit_text("✅ Все справочники синхронизированы")
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data == "cmd:load_suppliers")
async def callback_load_suppliers(callback: types.CallbackQuery):
    """Загрузка поставщиков"""
    await callback.answer()
    try:
        await sync_suppliers()
        await callback.message.edit_text("✅ Поставщики успешно синхронизированы")
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {str(e)}")


@router.callback_query(F.data == "cmd:load_accounts")
async def callback_load_accounts(callback: types.CallbackQuery):
    """Загрузка счетов"""
    await callback.answer()
    try:
        await sync_accounts()
        await callback.message.edit_text("✅ Счета успешно загружены в таблицу accounts")
    except Exception as e:
        await callback.message.edit_text(f"❌ Ошибка: {str(e)}")


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
    employees = await fetch_employees()
    await message.answer(f"👥 Загружено сотрудников: {len(employees)}")


# ───────────────────────────── /load_products ─────────────────────────────
@router.message(F.text == "/load_products")
async def load_products(message: Message):
    try:
        await init_db()
        data = await fetch_nomenclature()
        await sync_nomenclature(data)
        await sync_store_balances(data)  # <-- добавь этот вызов!
        await message.answer("✅ Номенклатура и балансы обновлены")
    except Exception as e:
        await safe_send_error(message, e)


# ─────────────────────────────── /load_groups ───────────────────────────────
@router.message(F.text == "/load_groups")
async def load_groups(message: Message):
    try:
        await init_groups_table()
        data = await fetch_groups()
        await sync_groups(data)
        await message.answer("✅ Группы номенклатуры обновлены")
    except Exception as e:
        await safe_send_error(message, e)

@router.message(F.text == "/load_stores")
async def load_stores(message: types.Message):
    try:
        await init_stores_table()        # создаём таблицу при необходимости
        data = await fetch_stores()      # тянем XML из iiko
        await sync_stores(data)          # upsert + удаление лишних
        await message.answer("✅ Склады обновлены")
    except Exception as e:
        await safe_send_error(message, e)


@router.message(F.text == "/load_references")
async def load_references(message: Message):
    try:
        await sync_all_references()
        await message.answer("✅ Все справочники синхронизированы")
    except Exception as e:
        await safe_send_error(message, e)


@router.message(F.text == "/load_supplyers")
async def sync_suppliers_command(message: Message):
    
    await sync_suppliers()
    await message.answer("🔄 Поставщики успешно синхронизированы.")


@router.message(F.text == "/load_accounts")
async def load_accounts_command(message: Message):
    try:
        await sync_accounts()
        await message.answer("✅ Счета успешно загружены в таблицу accounts.")
    except Exception as e:
        await safe_send_error(message, e)


@router.message(Command("cancel"))
async def cancel_any_state(message: Message, state: FSMContext):
    """Глобальная команда отмены — сбрасывает FSM и возвращает главное меню."""
    await state.clear()
    await message.answer(
        "❌ Действие отменено. Главное меню:",
        reply_markup=main_menu_keyboard(message.from_user.id),
    )