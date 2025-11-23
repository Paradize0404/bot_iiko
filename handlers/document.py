
## ────────────── Импорт библиотек и общих функций ──────────────
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from keyboards.main_keyboard import get_document_type_keyboard
from handlers.common import get_template_keyboard
from handlers.writeoff import start_writeoff 
from handlers.invoice import start_invoice
from config import ADMIN_IDS

## ────────────── Логгер и роутер для aiogram ──────────────
router = Router()



## ────────────── Старт создания документа ──────────────
@router.message(F.text == "Создание документа")
async def choose_document_type(message: Message, state: FSMContext):
    """
    Показывает меню выбора типа документа
    """
    kb = get_document_type_keyboard()
    msg = await message.answer("Выберите тип документа:", reply_markup=kb)
    await state.update_data(form_message_id=msg.message_id)



## ────────────── Запуск FSM списания ──────────────
@router.callback_query(F.data == "doc:writeoff")
async def handle_writeoff(callback: types.CallbackQuery, state: FSMContext):
    """
    Запускает FSM для акта списания
    """
    await start_writeoff(callback, state)  # 🧠 Запускаем FSM списания




## ────────────── Запуск FSM накладной ──────────────
@router.callback_query(F.data == "doc:invoice")
async def handle_invoice(callback: types.CallbackQuery, state: FSMContext):
    """
    Запускает FSM для расходной накладной
    """
    await start_invoice(callback, state)



## ────────────── Выбор действия для расходной накладной по шаблону ──────────────
@router.callback_query(F.data == "doc:prep")
async def forward_to_invoice_template(callback: types.CallbackQuery, state: FSMContext):
    """
    Показывает меню выбора действия для расходной накладной по шаблону
    """
    user_id = callback.from_user.id

    if user_id in ADMIN_IDS:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📋 По шаблону", callback_data="prep:by_template")],
            [InlineKeyboardButton(text="🛠 Создать шаблон", callback_data="prep:create_template")]
        ])
        await callback.message.edit_text("Выберите действие для расходной накладной:", reply_markup=keyboard)
        await callback.answer()
    else:
        # Для обычных пользователей сразу показываем список шаблонов
        from handlers.use_template import show_templates
        await show_templates(callback)





