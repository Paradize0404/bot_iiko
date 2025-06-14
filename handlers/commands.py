from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
# from states import RegisterStates
# from keyboards.main_menu import main_menu_keyboard
# from upload_db.sprav import get_token, fetch_reference, REFERENCE_TYPES
# from upload_db.db_sync import sync_reference_table
from bot import bot

router = Router()

@router.message(F.text == "/start")
async def start(message: Message, state: FSMContext):
    msg = await message.answer("Как тебя зовут?")
    await state.set_state(RegisterStates.waiting_for_name)
    await state.update_data(question_msg_id=msg.message_id)

@router.message(RegisterStates.waiting_for_name)
async def get_name(message: Message, state: FSMContext):
    try:
        await message.delete()
    except Exception:
        pass

    name = message.text
    data = await state.get_data()
    msg_id = data.get("question_msg_id")

    if msg_id:
        await bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=msg_id,
            text=f"Привет, {name} 👋"
        )

    await message.answer("Вот главное меню:", reply_markup=main_menu_keyboard())
    await state.clear()

@router.message(F.text == "/cancel")
async def cancel_process(message: Message, state: FSMContext):
    data = await state.get_data()

    msg_ids_to_delete = []
    for key in ["form_message_id", "question_msg_id", "quantity_prompt_message_id", "search_message_id", "user_text_id"]:
        if key in data:
            msg_ids_to_delete.append(data[key])

    for msg_id in msg_ids_to_delete:
        try:
            await bot.delete_message(chat_id=message.chat.id, message_id=msg_id)
        except Exception:
            pass

    try:
        await message.delete()
    except:
        pass

    await state.clear()
    await message.answer("❌ Действие отменено. Возвращаемся в главное меню.", reply_markup=main_menu_keyboard())
