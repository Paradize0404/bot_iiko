from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

def main_menu_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="💰 Зарплата")],
        [KeyboardButton(text="Команды")],
        [KeyboardButton(text="Создание документа")]
    ]
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
            [InlineKeyboardButton(text="🔄 Внутреннее перемещение", callback_data="doc:move")]
        ]
    )