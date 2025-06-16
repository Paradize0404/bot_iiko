# utils/warehouse_kb.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from typing import Sequence, Tuple

def make_keyboard(rows: Sequence[Tuple[str, str]]) -> ReplyKeyboardMarkup:
    """
    На входе — любой iterable из `(id, name)`.
    Кнопки — только `name`.  Id будем хранить в FSM.
    """
    kb = [[KeyboardButton(text=name)] for _, name in rows]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)
