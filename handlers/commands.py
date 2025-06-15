import logging
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from states import RegisterStates
from keyboards.main_keyboard import main_menu_keyboard
from services.employees import fetch_employees
from db.employees_db import async_session, Employee
from db.nomenclature_db import fetch_nomenclature, sync_nomenclature, init_db
from db.group_db import init_groups_table, fetch_groups, sync_groups
from utils.telegram_helpers import safe_send_error, tidy_response
from db.stores_db import (
    init_stores_table,
    fetch_stores,
    sync_stores,
)
logging.basicConfig(level=logging.INFO)

router = Router()


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
                old_msg_id=msg_id,           # редактируем вопрос-«фамилию?»
            )

            await message.answer(
                "Вот главное меню:",
                reply_markup=main_menu_keyboard(),
            )
            await state.clear()

        else:  # 🚫 Пользователь не найден
            warn_text = "🚫 Доступ запрещён. Сотрудник не найден."

            await tidy_response(
                message,
                warn_text,
                old_msg_id=msg_id,                        # редактируем вопрос
                reply_markup=types.ReplyKeyboardRemove(), # убираем клавиатуру
            )
            # остаёмся в RegisterStates.waiting_for_name


# ──────────────────────────────── /cancel ────────────────────────────────
@router.message(F.text == "/cancel")
async def cancel_process(message: Message, state: FSMContext):
    logging.info(f"❌ Отмена действия от {message.from_user.id}")
    data = await state.get_data()

    # чистим технические сообщения, сохранённые в state
    for key in [
        "form_message_id",
        "question_msg_id",
        "quantity_prompt_message_id",
        "search_message_id",
        "user_text_id",
    ]:
        msg_id = data.get(key)
        if msg_id:
            try:
                await message.bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
            except Exception:
                logging.warning(f"⚠️ Не удалось удалить сообщение {msg_id}")

    await state.clear()

    await tidy_response(
        message,
        "❌ Действие отменено. Возвращаемся в главное меню.",
        reply_markup=main_menu_keyboard(),
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
        await message.answer("✅ Номенклатура обновлена")
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