
## ────────────── Импорт библиотек и общих функций ──────────────

from abc import ABC, abstractmethod
from aiogram import Bot, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from db.employees_db import async_session, Employee
from handlers.common import get_unit_name_by_id
from services.db_queries import DBQueries
from config import DOC_CONFIG
import logging


## ────────────── Состояния FSM для базового документа ──────────────
class DocumentStates(StatesGroup):
    """Base FSM states for document workflows"""
    Store1 = State()
    Store2 = State()
    DocType = State()
    Comment = State()
    AddItems = State()
    Quantity = State()


## ────────────── Вспомогательная функция нормализации единиц ──────────────
def _normalize_unit(unit: str) -> str:
    """Normalize unit name to simple codes: 'kg', 'ml', 'l', 'шт', etc."""
    if not unit:
        return ""
    u = unit.strip().lower().replace('.', '')
    if u in ("кг", "kg", "килограмм", "килограмма", "килограммов"):
        return "kg"
    if u in ("мл", "ml", "миллилитр", "миллилитра", "миллилитров"):
        return "ml"
    if u in ("л", "l", "литр", "литра", "литров"):
        return "l"
    if u in ("шт", "шт", "штук", "штука"):
        return "шт"
    return u


## ────────────── Абстрактный базовый класс обработчика документа ──────────────
class BaseDocumentHandler(ABC):
    """Abstract base class for document handlers (writeoff, transfer, invoice, etc)"""

    doc_type: str = "document"  # Override in subclass
    states: StatesGroup = DocumentStates

    @abstractmethod
    async def get_store_keyboard(self, data: dict) -> InlineKeyboardMarkup:
        """Return keyboard for store selection"""
        pass

    @abstractmethod
    async def get_doc_type_keyboard(self, data: dict) -> InlineKeyboardMarkup:
        """Return keyboard for document type selection"""
        pass

    @abstractmethod
    async def format_header(self, data: dict) -> str:
        """Format header message for the document"""
        pass

    ## ────────────── Получение имени сотрудника по Telegram ID ──────────────
    async def get_employee_name(self, tg_id: str) -> str:
        """Get employee name by telegram ID"""
        user = await DBQueries.get_employee_by_telegram(tg_id)
        return f"{user.first_name} {user.last_name}" if user else "Неизвестно"

    ## ────────────── Обновление заголовка документа ──────────────
    async def update_header(
        self, bot: Bot, chat_id: int, msg_id: int, data: dict
    ) -> None:
        """Update document header message"""
        text = await self.format_header(data)
        items = data.get("items", [])

        if items:
            text += "\n<b>Товары:</b>\n"
            for i, item in enumerate(items, 1):
                unit = await get_unit_name_by_id(item['mainunit'])
                value = item.get("user_quantity", "—")
                norm = _normalize_unit(unit)
                
                if norm == "kg":
                    text += f"{i}. {item['name']} — <b>{value} г</b>\n"
                elif norm in ("l", "ml"):
                    text += f"{i}. {item['name']} — <b>{value} мл</b>\n"
                else:
                    text += f"{i}. {item['name']} — <b>{value} {unit}</b>\n"

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                parse_mode="HTML"
            )
        except Exception as e:
            logging.warning(f"⚠️ Не удалось обновить заголовок: {e}")

    ## ────────────── Формирование клавиатуры выбора товара ──────────────
    def build_item_keyboard(
        self, results: list[dict], callback_prefix: str
    ) -> InlineKeyboardMarkup:
        """Build keyboard for item selection"""
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=r['name'], callback_data=f"{callback_prefix}:{r['id']}")]
            for r in results
        ])
