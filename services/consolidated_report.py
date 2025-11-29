from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Dict

from services.purchase_insights import (
    PURCHASE_ACCOUNT_NAMES,
    PURCHASE_ACCOUNT_TYPES,
    calculate_purchase_metrics,
)
from services.purchase_summary import PurchaseSummary, get_purchase_summary
from services.revenue_report import (
    calculate_revenue,
    calculate_salary_by_departments,
    get_revenue_report,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ConsolidatedData:
    date_from: str
    date_to: str
    revenue_core: Decimal
    writeoff_revenue: Decimal
    total_revenue: Decimal
    kitchen_cost: Decimal
    kitchen_cost_percent: float | None
    bar_cost: Decimal
    bar_cost_percent: float | None
    cost_total: Decimal
    purchase_total: Decimal
    purchase_kitchen: Decimal
    purchase_bar: Decimal
    purchase_supplies: Decimal
    purchase_tmc: Decimal
    purchase_kitchen_bar: Decimal
    supplies_total: Decimal
    fot_total: Decimal
    dept_salaries: Dict[str, float]
    result_cost_based: Decimal
    result_purchase_based: Decimal


async def build_consolidated_report_text(reference_date: date | None = None) -> str:
    date_from, date_to = resolve_month_period(reference_date)
    summary = await _collect_data(date_from, date_to)
    return _format_report(summary)


def resolve_month_period(reference_date: date | None = None) -> tuple[str, str]:
    today = reference_date or datetime.now().date()
    if today.day == 1:
        raise ValueError("За текущий месяц ещё нет данных. Попробуйте запросить отчёт завтра.")
    period_end = today - timedelta(days=1)
    period_start = period_end.replace(day=1)
    return period_start.strftime("%Y-%m-%d"), period_end.strftime("%Y-%m-%d")


async def _collect_data(date_from: str, date_to: str) -> ConsolidatedData:
    summary = await get_purchase_summary(
        date_from,
        date_to,
        store_filter=PURCHASE_ACCOUNT_NAMES,
        account_type_filter=PURCHASE_ACCOUNT_TYPES,
    )

    revenue_rows = await get_revenue_report(date_from, date_to)
    revenue_data = await calculate_revenue(revenue_rows, date_from, date_to)
    metrics = await calculate_purchase_metrics(
        summary,
        date_from,
        date_to,
        revenue_rows=revenue_rows,
        revenue_data=revenue_data,
    )

    dept_salaries = await calculate_salary_by_departments(date_from, date_to)

    store_totals = summary.store_totals if isinstance(summary, PurchaseSummary) else {}
    purchase_kitchen = _decimal(store_totals.get("Кухня Пиццерия"))
    purchase_bar = _decimal(store_totals.get("Бар Пиццерия"))
    purchase_supplies = _decimal(store_totals.get("Хоз. товары Пиццерия"))
    purchase_tmc = _decimal(store_totals.get("ТМЦ Пиццерия"))
    purchase_total = _decimal(summary.total_amount)

    supplies_total = purchase_supplies + purchase_tmc
    purchase_kitchen_bar = purchase_kitchen + purchase_bar

    deviation = (metrics or {}).get("deviation") or {}
    kitchen_dev = deviation.get("kitchen") or {}
    bar_dev = deviation.get("bar") or {}

    kitchen_cost = _decimal(kitchen_dev.get("cost_value"))
    bar_cost = _decimal(bar_dev.get("cost_value"))
    kitchen_cost_percent = kitchen_dev.get("cost_percent")
    bar_cost_percent = bar_dev.get("cost_percent")

    if kitchen_cost == 0 and revenue_data.get("kitchen_total_cost"):
        kitchen_cost = _decimal(revenue_data.get("kitchen_total_cost"))
        kitchen_cost_percent = revenue_data.get("kitchen_total_cost_percent")
    if bar_cost == 0 and revenue_data.get("bar_cost"):
        bar_cost = _decimal(revenue_data.get("bar_cost"))
        bar_cost_percent = revenue_data.get("bar_cost_percent")

    cost_total = kitchen_cost + bar_cost

    revenue_core = (
        _decimal(revenue_data.get("bar_revenue"))
        + _decimal(revenue_data.get("kitchen_revenue"))
        + _decimal(revenue_data.get("delivery_revenue"))
    )
    writeoff_revenue = _decimal(revenue_data.get("writeoff_revenue"))
    total_revenue = revenue_core + writeoff_revenue

    fot_total = sum((_decimal(value) for value in (dept_salaries or {}).values()), Decimal("0"))

    result_cost_based = total_revenue - cost_total - fot_total - supplies_total
    result_purchase_based = total_revenue - purchase_kitchen_bar - fot_total - supplies_total

    return ConsolidatedData(
        date_from=date_from,
        date_to=date_to,
        revenue_core=revenue_core,
        writeoff_revenue=writeoff_revenue,
        total_revenue=total_revenue,
        kitchen_cost=kitchen_cost,
        kitchen_cost_percent=kitchen_cost_percent,
        bar_cost=bar_cost,
        bar_cost_percent=bar_cost_percent,
        cost_total=cost_total,
        purchase_total=purchase_total,
        purchase_kitchen=purchase_kitchen,
        purchase_bar=purchase_bar,
        purchase_supplies=purchase_supplies,
        purchase_tmc=purchase_tmc,
        purchase_kitchen_bar=purchase_kitchen_bar,
        supplies_total=supplies_total,
        fot_total=fot_total,
        dept_salaries=dept_salaries or {},
        result_cost_based=result_cost_based,
        result_purchase_based=result_purchase_based,
    )


def _format_report(data: ConsolidatedData) -> str:
    start_label = _fmt_date(data.date_from)
    end_label = _fmt_date(data.date_to)
    lines: list[str] = [
        "📊 *Сводный отчёт*",
        f"Период: {start_label} — {end_label}",
        "",
        f"💰 *Выручка*: {_fmt_currency(data.total_revenue)}",
        f"  • Основная (бар + кухня + доставка): {_fmt_currency(data.revenue_core)}",
        f"  • Расходные накладные: {_fmt_currency(data.writeoff_revenue)}",
        "",
        "📉 *Расходы*",
    ]

    cost_percent_total = _percent(data.cost_total, data.total_revenue)
    lines.append(
        f"• Себестоимость (кухня + бар): {_fmt_currency(data.cost_total)} ({_fmt_percent(cost_percent_total)})",
    )
    lines.append(
        f"  Кухня: {_fmt_currency(data.kitchen_cost)} ({_fmt_percent(data.kitchen_cost_percent)})",
    )
    lines.append(
        f"  Бар: {_fmt_currency(data.bar_cost)} ({_fmt_percent(data.bar_cost_percent)})",
    )

    fot_percent = _percent(data.fot_total, data.total_revenue)
    lines.append(f"• ФОТ (суммарно): {_fmt_currency(data.fot_total)} ({_fmt_percent(fot_percent)})")

    supplies_percent = _percent(data.supplies_total, data.total_revenue)
    lines.append(
        f"• ТМЦ + хознужды: {_fmt_currency(data.supplies_total)} ({_fmt_percent(supplies_percent)})",
    )

    purchase_kb_percent = _percent(data.purchase_kitchen_bar, data.total_revenue)
    lines.append(
        f"• Закуп (Кухня + Бар): {_fmt_currency(data.purchase_kitchen_bar)} ({_fmt_percent(purchase_kb_percent)})",
    )
    total_purchase_percent = _percent(data.purchase_total, data.total_revenue)
    lines.append(
        f"  Всего закуп по складам: {_fmt_currency(data.purchase_total)} ({_fmt_percent(total_purchase_percent)})",
    )

    supplies_breakdown = []
    if data.purchase_supplies:
        supplies_breakdown.append(f"хозы {_fmt_currency(data.purchase_supplies)}")
    if data.purchase_tmc:
        supplies_breakdown.append(f"ТМЦ {_fmt_currency(data.purchase_tmc)}")
    if supplies_breakdown:
        lines.append("  " + " / ".join(supplies_breakdown))

    lines.append("")
    lines.append("🧮 *Результат*")
    lines.append(
        "• Выручка − себестоимость − ФОТ − ТМЦ/хозы: "
        f"{_fmt_currency(data.result_cost_based)}",
    )
    lines.append(
        "• Выручка − закуп (Кухня+Бар) − ФОТ − ТМЦ/хозы: "
        f"{_fmt_currency(data.result_purchase_based)}",
    )

    return "\n".join(lines)


def _decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _fmt_currency(value: Decimal | float | int) -> str:
    amount = Decimal(value)
    return f"{amount:,.2f} ₽".replace(",", " ")


def _fmt_percent(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def _percent(amount: Decimal, base: Decimal) -> float | None:
    if base == 0:
        return None
    return float((amount / base) * 100)


def _fmt_date(date_str: str) -> str:
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")
    except ValueError:
        return date_str


__all__ = [
    "build_consolidated_report_text",
    "resolve_month_period",
]
