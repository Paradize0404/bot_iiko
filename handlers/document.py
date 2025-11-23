from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from keyboards.main_keyboard import get_document_type_keyboard
from handlers.common import get_template_keyboard
from handlers.writeoff import start_writeoff 
from handlers.invoice import start_invoice
from config import ADMIN_IDS

router = Router()



@router.message(F.text == "Создание документа")
async def choose_document_type(message: Message, state: FSMContext):
    kb = get_document_type_keyboard()
    msg = await message.answer("Выберите тип документа:", reply_markup=kb)
    await state.update_data(form_message_id=msg.message_id)



@router.callback_query(F.data == "doc:writeoff")
async def handle_writeoff(callback: types.CallbackQuery, state: FSMContext):
    await start_writeoff(callback, state)  # 🧠 Запускаем FSM списания




@router.callback_query(F.data == "doc:invoice")
async def handle_invoice(callback: types.CallbackQuery, state: FSMContext):
    await start_invoice(callback, state)



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





