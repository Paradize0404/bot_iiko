
## ────────────── Импорт библиотек и общих функций ──────────────
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from datetime import datetime
from keyboards.inline_calendar import build_calendar, parse_callback_data
from states import SalaryStates
from services.salary_from_iiko import get_salary_report_from_iiko
import logging

## ────────────── Логгер и роутер для aiogram ──────────────
logger = logging.getLogger(__name__)
router = Router()

## ────────────── Старт выбора периода для отчёта по зарплате ──────────────
@router.message(F.text == "💰 Зарплата")
async def salary_menu(message: Message, state: FSMContext):
    """
    Показывает календарь для выбора даты начала периода
    """
    today = datetime.today()
    calendar = build_calendar(today.year, today.month, calendar_id="salary_start", mode="single")
    await state.set_state(SalaryStates.selecting_start)
    await message.answer("Выберите дату начала периода:", reply_markup=calendar)

## ────────────── Обработка inline-календаря для даты начала ──────────────
@router.callback_query(F.data.startswith("CAL:salary_start"))
async def handle_salary_start_calendar(callback: CallbackQuery, state: FSMContext):
    """
    Обработка выбора даты начала периода
    """
    logger.debug("HANDLE_START_CALENDAR callback.data: %s", callback.data)
    cur_state = await state.get_state()
    logger.debug("FSM STATE (start): %s", cur_state)
    data = parse_callback_data(callback.data)
    logger.debug("PARSED DATA (start): %s", data)
    if not data or data["action"] == "IGNORE":
        await callback.answer()
        return

    if data["action"] in ["PREV", "NEXT"]:
        new_month = data["month"] - 1 if data["action"] == "PREV" else data["month"] + 1
        new_year = data["year"]
        if new_month == 0:
            new_month = 12
            new_year -= 1
        elif new_month == 13:
            new_month = 1
            new_year += 1
        calendar = build_calendar(new_year, new_month, calendar_id="salary_start", mode="single")
        await callback.message.edit_reply_markup(reply_markup=calendar)
        return

    if data["action"] == "DATE":
        selected_date = data["date"]
        logger.debug("SELECTED DATE (start): %s", selected_date)
        await state.update_data(from_date=selected_date.isoformat())
        await state.set_state(SalaryStates.selecting_end)
        today = datetime.today()
        calendar = build_calendar(today.year, today.month, calendar_id="salary_end", mode="single")
        await callback.message.edit_text("Теперь выберите дату окончания периода:", reply_markup=calendar)

## ────────────── Обработка inline-календаря для даты окончания ──────────────
@router.callback_query(F.data.startswith("CAL:salary_end"))
async def handle_salary_end_calendar(callback: CallbackQuery, state: FSMContext):
    """
    Обработка выбора даты окончания периода и формирование отчёта
    """
    logger.debug("HANDLE_END_CALENDAR callback.data: %s", callback.data)
    cur_state = await state.get_state()
    logger.debug("FSM STATE (end): %s", cur_state)
    data = parse_callback_data(callback.data)
    logger.debug("PARSED DATA (end): %s", data)
    if not data or data["action"] == "IGNORE":
        await callback.answer()
        return

    if data["action"] in ["PREV", "NEXT"]:
        new_month = data["month"] - 1 if data["action"] == "PREV" else data["month"] + 1
        new_year = data["year"]
        if new_month == 0:
            new_month = 12
            new_year -= 1
        elif new_month == 13:
            new_month = 1
            new_year += 1
        calendar = build_calendar(new_year, new_month, calendar_id="salary_end", mode="single")
        await callback.message.edit_reply_markup(reply_markup=calendar)
        return

    if data["action"] == "DATE":
        selected_date = data["date"]
        logger.debug("SELECTED DATE (end): %s", selected_date)
        state_data = await state.get_data()
        from_date = state_data.get("from_date")
        logger.debug("FROM_DATE from state (end): %s", from_date)
        to_date = selected_date.isoformat()
        from_dt, to_dt = sorted([
            datetime.fromisoformat(from_date).date(),
            selected_date
        ])
        await callback.message.edit_text("⏳ Формирую отчёт...")
        text = await get_salary_report_from_iiko(from_dt.isoformat(), to_dt.isoformat())
        await callback.message.answer(text)
        await state.clear()
