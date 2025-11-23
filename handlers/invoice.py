
## ────────────── Импорт библиотек и общих функций ──────────────

from aiogram import Router, types, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


## ────────────── Логгер и роутер для aiogram ──────────────
router = Router()

## ────────────── Состояния FSM для расходной накладной ──────────────
class IncomingInvoiceStates(StatesGroup):
    SelectType = State()
    SelectStore = State()
    SearchService = State()
    EnterServicePrice = State()
    SearchProduct = State()
    EnterProductQty = State()
    AddMoreOrFinish = State()
    Confirm = State()

# ТВОИ КОНСТАНТЫ
EXPENSE_STORE_ID = "..."     # ID "Расход Пиццерия" — подставь свой!
MAGAZIN_NAL_ID = "..."       # ID контрагента "Магазин нал" — подставь свой!

## ────────────── Старт создания расходной накладной ──────────────
async def start_invoice(callback: types.CallbackQuery, state: FSMContext):
    """
    Запуск FSM для расходной накладной
    """
    await state.clear()
    await state.set_state(IncomingInvoiceStates.SelectType)
    await callback.message.edit_text(
        "Выбери тип расхода:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Услуга", callback_data="inv_type:service")],
                [InlineKeyboardButton(text="Товар", callback_data="inv_type:goods")]
            ]
        )
    )

# 2. Обработка выбора типа расхода (услуга/товар)
## ────────────── Обработка выбора типа расхода ──────────────
@router.callback_query(F.data.startswith("inv_type:"))
async def handle_invoice_type(callback: types.CallbackQuery, state: FSMContext):
    """
    Обработка выбора типа расхода (услуга/товар)
    """
    invoice_type = callback.data.split(":")[1]
    await state.update_data(invoice_type=invoice_type)

    if invoice_type == "service":
        await state.update_data(store_id=EXPENSE_STORE_ID)
        await state.set_state(IncomingInvoiceStates.SearchService)
        await callback.message.edit_text("🔎 Введите часть названия услуги для поиска:")
    elif invoice_type == "goods":
        await state.set_state(IncomingInvoiceStates.SelectStore)
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Бар", callback_data="inv_store:bar")],
                [InlineKeyboardButton(text="Кухня", callback_data="inv_store:kitchen")],
                [InlineKeyboardButton(text="Кондитерский", callback_data="inv_store:confection")],
                [InlineKeyboardButton(text="Расходные материалы", callback_data="inv_store:materials")],
                [InlineKeyboardButton(text="Посуда", callback_data="inv_store:dishes")],
            ]
        )
        await callback.message.edit_text("🏬 На какой склад поступил товар?", reply_markup=keyboard)
    else:
        await callback.message.edit_text("❌ Неизвестный тип расхода.")
