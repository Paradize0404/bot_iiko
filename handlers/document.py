from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
  # production router обрабатывает сам
from keyboards.main_keyboard import get_document_type_keyboard
from handlers.use_template import get_template_keyboard
from handlers.use_template import handle_prep_choice
from handlers.writeoff import start_writeoff 
router = Router()

ADMIN_IDS = [1877127405, 6446544048]



@router.message(F.text == "Создание документа")
async def choose_document_type(message: Message, state: FSMContext):
    kb = get_document_type_keyboard()
    msg = await message.answer("Выберите тип документа:", reply_markup=kb)
    await state.update_data(form_message_id=msg.message_id)



@router.callback_query(F.data == "doc:writeoff")
async def handle_writeoff(callback: types.CallbackQuery, state: FSMContext):
    await start_writeoff(callback, state)  # 🧠 Запускаем FSM списания


@router.callback_query(F.data == "doc:move")
async def handle_move_placeholder(callback: types.CallbackQuery):
    await callback.answer("⛔️ Пока не реализовано", show_alert=True)



@router.callback_query(F.data == "doc:prep")
async def forward_to_prep(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    if user_id in ADMIN_IDS:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🧾 По шаблону", callback_data="prep:by_template")],
            [InlineKeyboardButton(text="🛠 Создать шаблон", callback_data="prep:create_template")]
        ])
        await callback.message.edit_text("Выберите действие для акта приготовления:", reply_markup=keyboard)
        await callback.answer()
    else:
        await handle_prep_choice(callback)  # 👈 Вызов из use_template





