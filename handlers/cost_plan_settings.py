"""Настройка месячных планов по себестоимости (бар и кухня+доставка)."""
from __future__ import annotations

import logging
from datetime import datetime, date

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from db.cost_plan_db import (
    init_cost_plan_table,
    upsert_cost_plan,
    get_month_plan_snapshot,
)
from keyboards.inline_calendar import build_calendar, parse_callback_data

router = Router()
logger = logging.getLogger(__name__)

SEGMENT_LABELS = {
    "bar": "Бар",
    "kitchen": "Кухня + доставка",
}


class CostPlanStates(StatesGroup):
    choosing_month = State()
    choosing_segment = State()
    entering_value = State()


def _segment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Бар", callback_data="plan_segment:bar")],
            [InlineKeyboardButton(text="Кухня + доставка", callback_data="plan_segment:kitchen")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="plan_segment:cancel")],
        ]
    )


def _fmt_percent(value: float | None) -> str:
    if value is None:
        return "не задан"
    return f"{value:.2f}%"


def _fmt_month(month_start: date) -> str:
    return month_start.strftime("%B %Y")


async def _load_month_snapshot(month_start: date) -> dict:
    try:
        await init_cost_plan_table()
        return await get_month_plan_snapshot(month_start)
    except RuntimeError:
        logger.warning("Пул БД не инициализирован — планы недоступны")
        return {"bar": None, "kitchen": None}


@router.message(F.text == "⚙️ План себестоимости")
async def start_plan_setup(message: types.Message, state: FSMContext):
    """Запуск мастера настройки плана."""
    today = datetime.now().date()
    await message.answer(
        "📅 Выберите месяц, для которого хотите задать план себестоимости:",
        reply_markup=build_calendar(
            year=today.year,
            month=today.month,
            calendar_id="plan_month",
            mode="single",
        ),
    )
    await state.set_state(CostPlanStates.choosing_month)


@router.callback_query(CostPlanStates.choosing_month, lambda c: c.data.startswith("CAL:plan_month"))
async def handle_plan_calendar(call: types.CallbackQuery, state: FSMContext):
    data = parse_callback_data(call.data)
    if not data or data["action"] == "IGNORE":
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
            reply_markup=build_calendar(year=year, month=month, calendar_id="plan_month", mode="single")
        )
        await call.answer()
        return

    if data["action"] == "DATE":
        month_start = data["date"].replace(day=1)
        await state.update_data(plan_month=month_start.isoformat())
        snapshot = await _load_month_snapshot(month_start)
        summary_text = (
            f"📅 Месяц: {_fmt_month(month_start)}\n\n"
            f"Текущие планы (в % себестоимости):\n"
            f"• Бар: {_fmt_percent(snapshot.get('bar'))}\n"
            f"• Кухня + доставка: {_fmt_percent(snapshot.get('kitchen'))}\n\n"
            "Куда устанавливаем план?"
        )
        await call.message.edit_text(summary_text, reply_markup=_segment_keyboard())
        await state.set_state(CostPlanStates.choosing_segment)
        await call.answer()
        return


@router.callback_query(CostPlanStates.choosing_segment, F.data.startswith("plan_segment:"))
async def select_segment(call: types.CallbackQuery, state: FSMContext):
    _, segment = call.data.split(":", 1)
    if segment == "cancel":
        await state.clear()
        await call.message.edit_text("❌ Настройка плана отменена")
        await call.answer()
        return

    if segment not in SEGMENT_LABELS:
        await call.answer("Неизвестный сегмент", show_alert=True)
        return

    await state.update_data(segment=segment)
    data = await state.get_data()
    month_label = _fmt_month(date.fromisoformat(data["plan_month"]))
    await call.message.answer(
        f"Введите план по себестоимости для *{SEGMENT_LABELS[segment]}* на {month_label} (в %):",
        parse_mode="Markdown",
    )
    await state.set_state(CostPlanStates.entering_value)
    await call.answer()


@router.message(CostPlanStates.entering_value)
async def save_plan_value(message: types.Message, state: FSMContext):
    raw_value = message.text.replace(" ", "").replace(",", ".")
    try:
        plan_value = float(raw_value)
    except ValueError:
        await message.answer("❌ Не удалось распознать число. Попробуйте ещё раз (например, 32 или 32.5)")
        return

    if plan_value < 0 or plan_value > 100:
        await message.answer("❌ План должен быть в диапазоне 0–100%. Попробуйте снова.")
        return

    data = await state.get_data()
    month = date.fromisoformat(data["plan_month"])
    segment = data["segment"]
    month_label = _fmt_month(month)

    try:
        await init_cost_plan_table()
        await upsert_cost_plan(month, segment, plan_value)
        snapshot = await get_month_plan_snapshot(month)
    except RuntimeError:
        await message.answer("❌ База данных недоступна. Попробуйте позже.")
        return

    await state.clear()

    await message.answer(
        f"✅ План для *{SEGMENT_LABELS[segment]}* на {month_label} сохранён: {_fmt_percent(plan_value)}",
        parse_mode="Markdown",
    )

    await message.answer(
        "Текущие значения:\n"
        f"• Бар: {_fmt_percent(snapshot.get('bar'))}\n"
        f"• Кухня + доставка: {_fmt_percent(snapshot.get('kitchen'))}"
    )
