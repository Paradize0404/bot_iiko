import logging
from datetime import datetime

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from keyboards.inline_calendar import build_calendar, parse_callback_data
from services.supplier_balance import get_supplier_balance, format_supplier_balance_report

router = Router()
logger = logging.getLogger(__name__)


class SupplierBalanceStates(StatesGroup):
    selecting_date = State()


@router.message(F.text == "📦 Баланс по поставщикам")
async def start_supplier_balance(message: types.Message, state: FSMContext):
    await message.answer(
        "Выберите дату отчёта:",
        reply_markup=build_calendar(
            year=datetime.now().year,
            month=datetime.now().month,
            calendar_id="sup_balance",
            mode="single",
        ),
    )
    await state.set_state(SupplierBalanceStates.selecting_date)


@router.callback_query(lambda c: c.data.startswith("CAL:sup_balance"))
async def supplier_balance_calendar(call: types.CallbackQuery, state: FSMContext):
    data = parse_callback_data(call.data)
    if not data:
        await call.answer()
        return

    # Листаем календарь
    if data["action"] in ["PREV", "NEXT"]:
        year = data["year"]
        month = data["month"]
        mode = data["mode"]
        if data["action"] == "PREV":
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        else:
            month += 1
            if month == 13:
                month = 1
                year += 1
        await call.message.edit_reply_markup(
            reply_markup=build_calendar(year, month, data["calendar_id"], mode)
        )
        await call.answer()
        return

    if data["action"] == "IGNORE":
        await call.answer()
        return

    if data["action"] != "DATE":
        await call.answer()
        return

    # Дата выбрана
    selected_date = data["date"].strftime("%d.%m.%Y")
    await state.clear()
    await call.answer()
    msg = await call.message.edit_text("⏳ Формируем баланс по поставщикам...")

    try:
        balance = await get_supplier_balance(selected_date)
        text = format_supplier_balance_report(balance, limit=None)
        await msg.edit_text(text)
    except Exception as e:
        logger.exception("Ошибка формирования баланса по поставщикам: %s", e)
        await msg.edit_text("❌ Не удалось получить баланс по поставщикам. Попробуйте позже.")
