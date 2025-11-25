## ────────────── Импорт библиотек и общих функций ──────────────
import logging
from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from utils.telegram_helpers import tidy_response
from config import ADMIN_IDS

## ────────────── Логгер и роутер для aiogram ──────────────
router = Router()

## ────────────── Функции создания клавиатур ──────────────
def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    keyboard = []

    if user_id in ADMIN_IDS:
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
            [InlineKeyboardButton(text="📦 Расходная накладная по шаблону", callback_data="doc:prep")],
            [InlineKeyboardButton(text="📉 Акт списания", callback_data="doc:writeoff")],
            [InlineKeyboardButton(text="🔄 Внутреннее перемещение", callback_data="doc:move")],
            [InlineKeyboardButton(text="💸 Создать расход", callback_data="doc:invoice")],
        ]
    )

def get_reports_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text='📈 Выручка / Себестоимость')],      # Новый отчёт
            [KeyboardButton(text='📑 Себестоимость по категориям')],  # Новый отчёт
            [KeyboardButton(text='💰 Зарплата')],
            [KeyboardButton(text='⚙️ Настройка комиссий')],           # Настройка комиссий сотрудников
            [KeyboardButton(text='⚙️ Комиссия Яндекс')],              # Настройка комиссии Яндекса
            [KeyboardButton(text='⚙️ План себестоимости')],
            [KeyboardButton(text='⚙️ Настройка цехов')],              # Настройка цехов и ФОТ
            [KeyboardButton(text='📝 Корректировка должности')],      # Корректировка истории должностей
            [KeyboardButton(text='📉 Списания')],
            [KeyboardButton(text='🔙 Назад')]
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите тип отчета"
    )


## ────────────── Обработчики команд ──────────────
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



