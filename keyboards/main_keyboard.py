import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from utils.telegram_helpers import tidy_response
router = Router()

# Список Telegram ID админов
ADMINS = [1877127405, 1059714785, 671785195]  # замените на реальные ID

def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    keyboard = []

    if user_id in ADMINS:
        keyboard.append([KeyboardButton(text="📊 Отчёты")])
        keyboard.append([KeyboardButton(text="Команды")])

    keyboard.append([KeyboardButton(text="Создание документа")])

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие"
    )

def get_document_type_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🧾 Акт приготовления", callback_data="doc:prep")],
            [InlineKeyboardButton(text="📉 Акт списания", callback_data="doc:writeoff")],
            [InlineKeyboardButton(text="🔄 Внутреннее перемещение", callback_data="doc:move")],
            [InlineKeyboardButton(text="💸 Создать расход", callback_data="doc:invoice")],  # ← ДОБАВЬ ЭТУ СТРОКУ
        ]
    )

def get_reports_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text='📈 Выручка / Себестоимость')],      # Новый отчёт
            [KeyboardButton(text='📑 Себестоимость по категориям')],  # Новый отчёт
            [KeyboardButton(text='💰 Зарплата')],
            [KeyboardButton(text='📉 Списания')],
            [KeyboardButton(text='🔙 Назад')]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите тип отчета"
    )


@router.message(F.text == "📊 Отчёты")
async def handle_reports_button(message: Message):
    await message.answer("Выберите тип отчета:", reply_markup=get_reports_keyboard())




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
        reply_markup=main_menu_keyboard(message.from_user.id),
    )




@router.message(F.text == "🔙 Назад")
async def handle_back_button(message: Message, state: FSMContext):
    await cancel_process(message, state)



