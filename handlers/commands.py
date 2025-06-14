import logging
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from states import RegisterStates
from keyboards.main_keyboard import main_menu_keyboard
from services.employees import fetch_employees
from db.employees_db import async_session, Employee
from db.nomenclature_db import fetch_products
logging.basicConfig(level=logging.INFO)

router = Router()

@router.message(F.text.startswith("/start"))
async def start(message: Message, state: FSMContext):
    logging.info(f"📨 /start от {message.from_user.id}")
    msg = await message.answer("Как тебя зовут (Напиши свою фамилию)?")
    await state.set_state(RegisterStates.waiting_for_name)
    await state.update_data(question_msg_id=msg.message_id)

@router.message(RegisterStates.waiting_for_name)
async def get_name(message: Message, state: FSMContext):
    logging.info(f"👤 Фамилия получена от {message.from_user.id}: {message.text}")
    try:
        await message.delete()
    except Exception as e:
        logging.warning(f"❗️Не удалось удалить сообщение: {e}")

    last_name = message.text.strip()
    data = await state.get_data()
    msg_id = data.get("question_msg_id")

    async with async_session() as session:
        result = await session.execute(
            Employee.__table__.select().where(Employee.last_name == last_name)
        )
        row = result.fetchone()

        if row:
            employee = await session.get(Employee, row[0])
            employee.telegram_id = str(message.from_user.id)
            await session.commit()
            greet_text = f"Привет, {employee.first_name} 👋"

            if msg_id:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=msg_id,
                    text=greet_text
                )
            else:
                await message.answer(greet_text)

            await message.answer("Вот главное меню:", reply_markup=main_menu_keyboard())
            await state.clear()

        else:
            warn_text = "🚫 Доступ запрещён. Сотрудник не найден."

            if msg_id:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=msg_id,
                    text=warn_text
                )
            else:
                await message.answer(warn_text)

            # ❌ Удаляем клавиатуру
            await message.answer(warn_text, reply_markup=types.ReplyKeyboardRemove())

            # ❗️Не сбрасываем state, остаётся в RegisterStates.waiting_for_name

            # остаётся в состоянии ожидания

@router.message(F.text == "/cancel")
async def cancel_process(message: Message, state: FSMContext):
    logging.info(f"❌ Отмена действия от {message.from_user.id}")
    data = await state.get_data()

    msg_ids_to_delete = []
    for key in ["form_message_id", "question_msg_id", "quantity_prompt_message_id", "search_message_id", "user_text_id"]:
        if key in data:
            msg_ids_to_delete.append(data[key])

    for msg_id in msg_ids_to_delete:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
        except Exception:
            logging.warning(f"⚠️ Не удалось удалить сообщение {msg_id}")

    try:
        await message.delete()
    except Exception:
        pass

    await state.clear()
    await message.answer("❌ Действие отменено. Возвращаемся в главное меню.", reply_markup=main_menu_keyboard())



@router.message(F.text == "/load_staff")
async def load_staff(message: Message):
    employees = await fetch_employees()
    await message.answer(f"👥 Загружено сотрудников: {len(employees)}")


# @router.message(F.text == "/load_products")
# async def load_products(message: Message):
#     await message.answer("⏳ Запрашиваю номенклатуру…")
#     try:
#         products = await fetch_products()
#         await init_db()           # создаём таблицу, если надо
#         await save_products(products)
#         await message.answer(f"✅ Номенклатура обновлена, всего позиций: {len(products)}")
#     except Exception as e:
#         await message.answer(f"❌ Ошибка при загрузке: {e}")