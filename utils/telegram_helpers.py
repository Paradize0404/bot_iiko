"""Utility helpers for cleaner Telegram (AIogram) interactions.

The aim is to keep chat noise to a minimum by *editing* the bot’s last
message instead of sending a new one, and by *deleting* the triggering
user message when appropriate.
"""

from aiogram import types
import io
import traceback
from typing import Any, Optional
from aiogram.types import Message
from aiogram.exceptions import TelegramBadRequest

__all__ = [
    "MAX_TG_LEN",
    "safe_send_error",
    "tidy_response",
]

# ────────────────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────────────────
MAX_TG_LEN = 3500  # запас под эмодзи/заголовок

# ────────────────────────────────────────────────────────────────────────────────
# Public helpers
# ────────────────────────────────────────────────────────────────────────────────

async def safe_send_error(msg: types.Message, exc: Exception) -> None:
    """Reply with an error message, falling back to a *file* if too long.

    Parameters
    ----------
    msg:
        The incoming (user) message that caused the error.
    exc:
        The exception instance you want to show.
    """
    text = f"❌ Ошибка:\n{exc}"
    if len(text) <= MAX_TG_LEN:
        await msg.answer(text)
    else:
        buf = io.BytesIO(traceback.format_exc().encode())
        buf.name = "error.txt"
        await msg.answer_document(buf, caption="❌ Подробный стек-трейс")


async def tidy_response(
    trigger: types.Message,
    text: str,
    *,
    old_msg_id: Optional[int] = None,
    reply_markup: Optional[types.ReplyKeyboardMarkup | types.InlineKeyboardMarkup] = None,
    parse_mode: Optional[str] = None,
) -> types.Message:
    """
    • пробует удалить триггер-сообщение пользователя;
    • если передан old_msg_id — пытается отредактировать его;
    • если редактирование не удалось или id не передан — отправляет новый ответ.
    """
    try:
        await trigger.delete()
    except Exception:
        pass

    bot = trigger.bot
    chat_id = trigger.chat.id

    if old_msg_id:
        try:
            return await bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=old_msg_id,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        except Exception:
            # молча падаем в fallback ⇒ новое сообщение
            pass

    return await trigger.answer(text, reply_markup=reply_markup, parse_mode=parse_mode)



async def edit_or_send(message: Message, message_id: int, new_text: str):
    try:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=message_id,
            text=new_text
        )
    except TelegramBadRequest:
        # если старое сообщение удалить нельзя (например, оно исчезло) — отправляем новое
        sent = await message.answer(new_text)
        return sent.message_id
    return message_id