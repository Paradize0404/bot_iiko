import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State

from services.revenue_report import (
    analyze_cost_by_cooking_place,
    calculate_revenue,
    calculate_salary_by_departments,
    format_cost_by_cooking_place_report,
    format_dishes_table,
    format_l4l_revenue_report,
    format_revenue_report,
    get_revenue_report,
)
from keyboards.inline_calendar import build_calendar, parse_callback_data

## ────────────── Логгер и роутер для aiogram ──────────────
router = Router()


## ────────────── Состояния FSM для отчёта по продажам ──────────────
class SalesReportStates(StatesGroup):
    selecting_start = State()
    selecting_end = State()


logger = logging.getLogger(__name__)

## ────────────── Маршруты aiogram для отчёта ──────────────

# Кнопка: 📈 Выручка / Себестоимость
@router.message(F.text == "📈 Выручка / Себестоимость")
async def start_main_report(message: types.Message, state: FSMContext):
    """
    Старт отчёта по выручке и себестоимости
    """
    await message.answer("Выберите дату *начала* периода:", reply_markup=build_calendar(
        year=datetime.now().year, month=datetime.now().month, calendar_id="sales_main_start", mode="single"
    ))
    await state.set_state(SalesReportStates.selecting_start)
    await state.update_data(report_type="main")


# Кнопка: L4L (YoY)
@router.message(F.text == "L4L")
async def start_l4l_report(message: types.Message, state: FSMContext):
    """Запросить L4L отчёт без расчёта ФОТ (YoY)."""

    await message.answer("Выберите дату *начала* периода:", reply_markup=build_calendar(
        year=datetime.now().year, month=datetime.now().month, calendar_id="sales_l4l_start", mode="single"
    ))
    await state.set_state(SalesReportStates.selecting_start)
    await state.update_data(report_type="l4l")

@router.message(F.text == "📑 Себестоимость по категориям")
async def start_category_report(message: types.Message, state: FSMContext):
    """
    Старт отчёта по категориям
    """
    await message.answer("Выберите дату *начала* периода:", reply_markup=build_calendar(
        year=datetime.now().year, month=datetime.now().month, calendar_id="sales_cat_start", mode="single"
    ))
    await state.set_state(SalesReportStates.selecting_start)
    await state.update_data(report_type="category")

@router.callback_query(lambda c: c.data.startswith("CAL:sales"))
async def calendar_handler(call: types.CallbackQuery, state: FSMContext):
    """
    Обработка inline-календаря для выбора дат отчёта
    """
    data = parse_callback_data(call.data)
    if not data or data["action"] == "IGNORE":
        await call.answer()
        return

    cur_state = await state.get_state()

    # Листаем календарь
    if data["action"] in ["PREV", "NEXT"]:
        year = data["year"]
        month = data["month"]
        mode = data["mode"]
        calendar_id = data["calendar_id"]
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
        await call.message.edit_reply_markup(reply_markup=build_calendar(year, month, calendar_id, mode))
        await call.answer()
        return

    # Если выбран день
    if data["action"] == "DATE":
        # Сохраняем в формате YYYY-MM-DD для API
        selected_date_api = data["date"].strftime("%Y-%m-%d")
        # Форматируем для отображения пользователю
        selected_date_display = data["date"].strftime("%d.%m.%Y")
        user_data = await state.get_data()
        report_type = user_data.get("report_type")

        if cur_state == SalesReportStates.selecting_start.state:
            await state.update_data(date_start=selected_date_api)
            await state.set_state(SalesReportStates.selecting_end)
            await call.message.edit_text(f"Дата начала: {selected_date_display}\nТеперь выберите дату *конца* периода:", reply_markup=build_calendar(
                year=data["date"].year, month=data["date"].month, calendar_id="sales_end", mode="single"
            ))
            await call.answer()
            return

        elif cur_state == SalesReportStates.selecting_end.state:
            await state.update_data(date_end=selected_date_api)
            data_ctx = await state.get_data()
            await state.clear()
            
            # Сразу отвечаем на callback, чтобы избежать timeout
            await call.answer()

            # Запуск генерации отчёта
            msg = await call.message.edit_text("⏳ Формируем отчёт... Пожалуйста, подождите.")

            report_type = data_ctx.get("report_type")

            if report_type == "main":
                # Отчет по выручке (только OLAP, с ФОТ)
                try:
                    raw_data = await get_revenue_report(
                        date_from=data_ctx["date_start"],
                        date_to=data_ctx["date_end"]
                    )

                    revenue_data = await calculate_revenue(
                        raw_data,
                        data_ctx["date_start"],
                        data_ctx["date_end"]
                    )

                    dept_salaries = None
                    try:
                        dept_salaries = await calculate_salary_by_departments(
                            data_ctx["date_start"],
                            data_ctx["date_end"],
                        )
                    except Exception as exc:
                        logger.warning("Не удалось рассчитать ФОТ по цехам: %s", exc)

                    text = format_revenue_report(
                        revenue_data,
                        data_ctx["date_start"],
                        data_ctx["date_end"],
                        dept_salaries=dept_salaries,
                    )
                    await msg.edit_text(text, parse_mode="Markdown")
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Ошибка при формировании отчета: %s", exc)
                    await msg.edit_text(f"❌ Ошибка при формировании отчета: {exc}")

            elif report_type == "l4l":
                # Сравнение периода с аналогичным периодом прошлого года, без ФОТ
                try:
                    def _shift_one_year(date_str: str) -> str:
                        base_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                        try:
                            shifted = base_date.replace(year=base_date.year - 1)
                        except ValueError:
                            shifted = base_date - timedelta(days=365)
                        return shifted.strftime("%Y-%m-%d")

                    prev_start = _shift_one_year(data_ctx["date_start"])
                    prev_end = _shift_one_year(data_ctx["date_end"])

                    current_raw_task = asyncio.create_task(get_revenue_report(
                        date_from=data_ctx["date_start"],
                        date_to=data_ctx["date_end"],
                    ))
                    prev_raw_task = asyncio.create_task(get_revenue_report(
                        date_from=prev_start,
                        date_to=prev_end,
                    ))
                    current_raw, prev_raw = await asyncio.gather(current_raw_task, prev_raw_task)

                    current_calc_task = asyncio.create_task(calculate_revenue(
                        current_raw,
                        data_ctx["date_start"],
                        data_ctx["date_end"],
                    ))
                    prev_calc_task = asyncio.create_task(calculate_revenue(
                        prev_raw,
                        prev_start,
                        prev_end,
                    ))
                    current_data, prev_data = await asyncio.gather(current_calc_task, prev_calc_task)

                    text = format_l4l_revenue_report(
                        current_data,
                        prev_data,
                        data_ctx["date_start"],
                        data_ctx["date_end"],
                        prev_start,
                        prev_end,
                    )
                    await msg.edit_text(text, parse_mode="Markdown")
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Ошибка при формировании L4L отчета: %s", exc)
                    await msg.edit_text(f"❌ Ошибка при формировании L4L отчета: {exc}")

            elif report_type == "category":
                try:
                    cost_data = await analyze_cost_by_cooking_place(
                        data_ctx["date_start"],
                        data_ctx["date_end"],
                    )
                    text = format_cost_by_cooking_place_report(cost_data)
                    logger.info("\n%s", text.replace("*", ""))
                    await msg.edit_text(text, parse_mode="Markdown")
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Ошибка при расчёте себестоимости по местам приготовления: %s", exc)
                    await msg.edit_text(f"❌ Ошибка при формировании отчета: {exc}")

            else:
                await msg.edit_text("❌ Неизвестный тип отчёта. Попробуйте ещё раз.")
            
            return


## ────────────── Точка входа для консольного запуска ──────────────
async def main():
    today = datetime.now().date()
    week_ago = today - timedelta(days=7)
    date_from = week_ago.strftime("%Y-%m-%d")
    date_to = today.strftime("%Y-%m-%d")
    logger.info("Консольный расчёт себестоимости по местам приготовления: %s — %s", date_from, date_to)
    cost_data = await analyze_cost_by_cooking_place(date_from, date_to)
    text = format_cost_by_cooking_place_report(cost_data)
    print(text)
    logger.info("\n%s", text.replace("*", ""))
    dishes = cost_data.get('dishes', {})
    segments = (("bar", "Бар"), ("kitchen", "Кухня (вкл. пиццу)"), ("delivery", "Доставка (Яндекс)"))
    for segment_key, title in segments:
        segment_dishes = dishes.get(segment_key) or {}

        top_positive = segment_dishes.get('top_positive') or []
        print(f"\nТОП-5 положительных блюд — {title}")
        print(format_dishes_table(top_positive, limit=5))

        top_negative = segment_dishes.get('top_negative') or []
        print(f"\nТОП-5 отрицательных блюд — {title}")
        print(format_dishes_table(top_negative, limit=5))

        full_records = segment_dishes.get('full', [])
        print(f"\nПолный список блюд — {title}")
        print(format_dishes_table(full_records))

if __name__ == "__main__":
    asyncio.run(main())
