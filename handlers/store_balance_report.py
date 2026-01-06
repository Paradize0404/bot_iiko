import logging
from datetime import datetime

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from keyboards.inline_calendar import build_calendar, parse_callback_data
from services.store_balance_report import build_store_balance_text


logger = logging.getLogger(__name__)
router = Router()


class StoreBalanceStates(StatesGroup):
    selecting_start = State()
    selecting_end = State()


CALENDAR_PREFIX = "store_balance"
ERROR_HINT = (
    "⚠️ Не удалось сформировать отчёт по остаткам."
    "\nПопробуйте ещё раз чуть позже."
)


@router.message(F.text == "📦 Остатки Бар/Кухня")
async def start_store_balance_report(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(StoreBalanceStates.selecting_start)
    now = datetime.now()
    await message.answer(
        "Выберите дату *начала* периода:",
        reply_markup=build_calendar(
            year=now.year,
            month=now.month,
            calendar_id=f"{CALENDAR_PREFIX}_start",
            mode="single",
        ),
    )


@router.callback_query(lambda c: c.data and c.data.startswith(f"CAL:{CALENDAR_PREFIX}"))
async def store_balance_calendar(call: types.CallbackQuery, state: FSMContext):
    data = parse_callback_data(call.data)
    if not data:
        await call.answer()
        return

    if data["action"] == "IGNORE":
        await call.answer()
        return

    if data["action"] in {"PREV", "NEXT"}:
        year = data["year"]
        month = data["month"]
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
            reply_markup=build_calendar(
                year=year,
                month=month,
                calendar_id=data["calendar_id"],
                mode=data["mode"],
            )
        )
        await call.answer()
        return

    if data["action"] != "DATE":
        await call.answer()
        return

    current_state = await state.get_state()
    if current_state is None:
        await call.answer("Сессия отчёта устарела. Начните заново.", show_alert=True)
        return

    selected_iso = data["date"].strftime("%Y-%m-%d")
    selected_display = data["date"].strftime("%d.%m.%Y")

    if current_state == StoreBalanceStates.selecting_start.state:
        await state.update_data(date_start=selected_iso)
        await state.set_state(StoreBalanceStates.selecting_end)
        await call.message.edit_text(
            f"Дата начала: {selected_display}\nТеперь выберите дату *конца* периода:",
            reply_markup=build_calendar(
                year=data["date"].year,
                month=data["date"].month,
                calendar_id=f"{CALENDAR_PREFIX}_end",
                mode="single",
            ),
        )
        await call.answer()
        return

    if current_state != StoreBalanceStates.selecting_end.state:
        await call.answer("Сессия отчёта устарела. Начните заново.", show_alert=True)
        await state.clear()
        return

    user_data = await state.get_data()
    date_start = user_data.get("date_start")
    date_end = selected_iso
    if not date_start:
        await call.answer("Не найдена дата начала. Начните заново.", show_alert=True)
        await state.clear()
        return

    if date_end < date_start:
        date_start, date_end = date_end, date_start

    await state.clear()
    await call.answer()

    msg = await call.message.edit_text("⏳ Считаем остатки по бару и кухне...")
    try:
        text = await build_store_balance_text(date_start, date_end)
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка при формировании отчёта по остаткам: %s", exc)
        await msg.edit_text(f"{ERROR_HINT}\n\nТехническая информация: {exc}")
