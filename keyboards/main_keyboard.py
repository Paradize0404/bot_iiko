from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

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


def document_menu_keyboard() -> ReplyKeyboardMarkup:
    """
    Клавиатура, которая появляется после нажатия «Создание документа».
    """
    keyboard = [
        [KeyboardButton(text="🧾 Акт приготовления")],
        [KeyboardButton(text="📉 Акт списания")],
        [KeyboardButton(text="🔄 Внутреннее перемещение")],
        [KeyboardButton(text="⬅️ Назад")],  # кнопка возврата в главное меню
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите тип документа",
    )