import logging
from datetime import datetime
from decimal import Decimal

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from keyboards.inline_calendar import build_calendar, parse_callback_data
from services.consolidated_report import build_consolidated_report_text
from services.purchase_insights import (
    PURCHASE_ACCOUNT_NAMES,
    PURCHASE_ACCOUNT_TYPES,
    calculate_purchase_metrics,
)
from services.purchase_summary import get_purchase_summary
from services.revenue_report import get_revenue_report, calculate_revenue
from services.supplies_tmc_report import (
    DEFAULT_ACCOUNT_FILTERS as SUPPLIES_ACCOUNT_ORDER,
    get_supplies_tmc_report,
    split_rows_by_account,
)

logger = logging.getLogger(__name__)
router = Router()


class PurchaseReportStates(StatesGroup):
    selecting_start = State()
    selecting_end = State()


class SuppliesTmcReportStates(StatesGroup):
    selecting_start = State()
    selecting_end = State()


PURCHASE_CALENDAR_PREFIX = "purchase"
SUPPLIES_TMC_CALENDAR_PREFIX = "supplies_tmc"
PURCHASE_ERROR_HINT = (
    "⚠️ Не удалось получить данные от iiko."
    "\nПожалуйста, попробуйте ещё раз через пару минут — бот работает нормально,"
    " просто внешний сервис временно недоступен."
)
SUPPLIES_TMC_ERROR_HINT = (
    "⚠️ Не удалось получить данные по расходным материалам / ТМЦ."
    "\nПопробуйте ещё раз чуть позже."
)
CONSOLIDATED_ERROR_HINT = (
    "⚠️ Не удалось сформировать сводный отчёт."
    "\nПопробуйте ещё раз чуть позже."
)


def _fmt_currency(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ")


def _fmt_date(date_str: str) -> str:
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")


def _fmt_percent(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def _format_summary_text(
    summary,
    date_from: str,
    date_to: str,
    metrics: dict[str, dict[str, float]] | None,
) -> str:
    period_text = f"{_fmt_date(date_from)} — {_fmt_date(date_to)}"
    lines = [
        "📦 *Закуп по складам*",
        f"Период: {period_text}",
    ]

    if not summary.rows_count:
        lines.append("Данных за выбранный период нет.")
        return "\n".join(lines)

    lines.append(f"Итого: *{_fmt_currency(summary.total_amount)} ₽*")
    lines.append("")
    lines.append("*Склады:*")
    if summary.store_totals:
        for store, amount in sorted(summary.store_totals.items(), key=lambda item: item[1], reverse=True):
            lines.append(f"- {store}: {_fmt_currency(amount)} ₽")
    else:
        lines.append("- Нет данных по складам")

    share_info = (metrics or {}).get("share")
    if share_info:
        lines.append("")
        lines.append("*Доля закупа от выручки:*")

        def _append_share(label: str, key: str) -> None:
            percent = share_info.get(f"{key}_percent")
            if percent is None:
                return
            purchase_value = Decimal(str(share_info.get(f"{key}_purchase", 0)))
            base_value = Decimal(str(share_info.get(f"{key}_base", 0)))
            lines.append(
                f"- {label}: {_fmt_percent(percent)} "
                f"(закуп {_fmt_currency(purchase_value)} ₽ / база {_fmt_currency(base_value)} ₽)"
            )

        _append_share("Кухня", "kitchen")
        _append_share("Бар", "bar")
        _append_share("Хоз. товары", "supplies")
        _append_share("ТМЦ", "tmc")
        _append_share("Все склады", "total")

    deviation_info = (metrics or {}).get("deviation")
    if deviation_info:
        lines.append("")
        lines.append("*Отклонение закупа от себестоимости:*")

        def _append_deviation(label: str, key: str) -> None:
            entry = deviation_info.get(key) if deviation_info else None
            if not entry:
                return

            purchase_percent = entry.get("purchase_percent")
            cost_percent = entry.get("cost_percent")
            cost_value = entry.get("cost_value")
            deviation = entry.get("deviation")
            if purchase_percent is None or cost_percent is None:
                return

            lines.append(
                f"- {label}: закуп {_fmt_percent(purchase_percent)} vs себестоимость {_fmt_percent(cost_percent)} "
                f"({_fmt_currency(Decimal(str(cost_value or 0)))} ₽) → "
                f"{deviation:+.1f} п.п."
            )

        _append_deviation("Кухня", "kitchen")
        _append_deviation("Бар", "bar")

    return "\n".join(lines)


def _format_supplies_tmc_text(report, date_from: str, date_to: str) -> str:
    start_label = _fmt_date(date_from)
    end_label = _fmt_date(date_to)
    period_label = start_label if date_from == date_to else f"{start_label} — {end_label}"
    lines = [
        "📦 *Расходные материалы / ТМЦ*",
        f"Период: {period_label}",
        "",
    ]

    if not report.rows:
        lines.append("Данных за выбранный период нет.")
        return "\n".join(lines)

    blocks = split_rows_by_account(report.rows, SUPPLIES_ACCOUNT_ORDER)
    if not blocks:
        lines.append("Данных за выбранный период нет.")
        return "\n".join(lines)

    for block in blocks:
        lines.append(f"*Счёт:* {block.account_name}")
        for row in block.rows:
            lines.append(f"• {row.group_label}: {_fmt_currency(row.amount)} ₽")
        lines.append(f"_Итого по счёту:_ {_fmt_currency(block.total)} ₽")
        lines.append("")

    lines.append(f"*Общая сумма прихода:* {_fmt_currency(report.total_amount)} ₽")
    return "\n".join(lines)


@router.message(F.text == "📦 Закуп по складам")
async def start_purchase_report(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(PurchaseReportStates.selecting_start)
    now = datetime.now()
    await message.answer(
        "Выберите дату *начала* периода:",
        reply_markup=build_calendar(
            year=now.year,
            month=now.month,
            calendar_id=f"{PURCHASE_CALENDAR_PREFIX}_start",
            mode="single",
        ),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("CAL:purchase"))
async def purchase_calendar_handler(call: types.CallbackQuery, state: FSMContext):
    data = parse_callback_data(call.data)
    if not data:
        await call.answer()
        return

    calendar_id = data["calendar_id"]
    if not calendar_id.startswith(PURCHASE_CALENDAR_PREFIX):
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

    selected_date_iso = data["date"].strftime("%Y-%m-%d")
    selected_date_display = data["date"].strftime("%d.%m.%Y")

    if current_state == PurchaseReportStates.selecting_start.state:
        await state.update_data(date_start=selected_date_iso)
        await state.set_state(PurchaseReportStates.selecting_end)
        await call.message.edit_text(
            f"Дата начала: {selected_date_display}\nТеперь выберите дату *конца* периода:",
            reply_markup=build_calendar(
                year=data["date"].year,
                month=data["date"].month,
                calendar_id=f"{PURCHASE_CALENDAR_PREFIX}_end",
                mode="single",
            ),
        )
        await call.answer()
        return

    if current_state != PurchaseReportStates.selecting_end.state:
        await call.answer()
        return

    user_data = await state.get_data()
    date_start = user_data.get("date_start")
    date_end = selected_date_iso
    if not date_start:
        await call.answer("Не найдена дата начала. Начните заново.", show_alert=True)
        await state.clear()
        return

    # Ensure chronological order
    if date_end < date_start:
        date_start, date_end = date_end, date_start

    await state.clear()
    await call.answer()

    msg = await call.message.edit_text("⏳ Формируем отчёт по закупкам... Подождите.")
    try:
        summary = await get_purchase_summary(
            date_start,
            date_end,
            store_filter=PURCHASE_ACCOUNT_NAMES,
            account_type_filter=PURCHASE_ACCOUNT_TYPES,
        )
        metrics = None
        if summary.rows_count:
            metrics = await calculate_purchase_metrics(summary, date_start, date_end)
        text = _format_summary_text(summary, date_start, date_end, metrics)
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as exc:
        logger.exception("Ошибка при формировании отчёта по закупкам: %s", exc)
        await msg.edit_text(f"{PURCHASE_ERROR_HINT}\n\nТехническая информация: {exc}")


@router.message(F.text == "Расходные материалы/ТМЦ")
async def start_supplies_tmc_report(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(SuppliesTmcReportStates.selecting_start)
    now = datetime.now()
    await message.answer(
        "Выберите дату *начала* периода для отчёта по расходным материалам / ТМЦ:",
        reply_markup=build_calendar(
            year=now.year,
            month=now.month,
            calendar_id=f"{SUPPLIES_TMC_CALENDAR_PREFIX}_start",
            mode="single",
        ),
    )


@router.callback_query(lambda c: c.data and c.data.startswith("CAL:supplies_tmc"))
async def supplies_tmc_calendar_handler(call: types.CallbackQuery, state: FSMContext):
    data = parse_callback_data(call.data)
    if not data:
        await call.answer()
        return

    calendar_id = data["calendar_id"]
    if not calendar_id.startswith(SUPPLIES_TMC_CALENDAR_PREFIX):
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

    if current_state == SuppliesTmcReportStates.selecting_start.state:
        await state.update_data(supplies_start=selected_iso)
        await state.set_state(SuppliesTmcReportStates.selecting_end)
        await call.message.edit_text(
            f"Дата начала: {selected_display}\nТеперь выберите дату *конца* периода:",
            reply_markup=build_calendar(
                year=data["date"].year,
                month=data["date"].month,
                calendar_id=f"{SUPPLIES_TMC_CALENDAR_PREFIX}_end",
                mode="single",
            ),
        )
        await call.answer()
        return

    if current_state != SuppliesTmcReportStates.selecting_end.state:
        await call.answer("Сессия отчёта устарела. Начните заново.", show_alert=True)
        await state.clear()
        return

    user_data = await state.get_data()
    date_start = user_data.get("supplies_start")
    date_end = selected_iso
    if not date_start:
        await call.answer("Не найдена дата начала. Начните заново.", show_alert=True)
        await state.clear()
        return

    if date_end < date_start:
        date_start, date_end = date_end, date_start

    await state.clear()
    await call.answer()

    msg = await call.message.edit_text("⏳ Формируем отчёт по расходным материалам / ТМЦ...")
    try:
        report = await get_supplies_tmc_report(date_start, date_end)
        text = _format_supplies_tmc_text(report, date_start, date_end)
        await msg.edit_text(text, parse_mode="Markdown")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка при формировании отчёта supplies/tmc: %s", exc)
        await msg.edit_text(f"{SUPPLIES_TMC_ERROR_HINT}\n\nТехническая информация: {exc}")


@router.message(F.text == "📊 Сводный отчет")
async def send_consolidated_report(message: types.Message, state: FSMContext):
    await state.clear()
    try:
        text = await build_consolidated_report_text()
        await message.answer(text, parse_mode="Markdown")
    except ValueError as exc:
        await message.answer(str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка при формировании сводного отчёта: %s", exc)
        await message.answer(CONSOLIDATED_ERROR_HINT)
