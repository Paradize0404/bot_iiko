from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from keyboards.inline_calendar import build_calendar, parse_callback_data
from datetime import datetime
import httpx
import logging
from sqlalchemy import select
from db.employees_db import async_session
from handlers.writeoff import Accounts
from iiko.iiko_auth import get_auth_token, get_base_url
from handlers.template_creation import preload_stores, STORE_CACHE
from handlers.common import Store
from services.revenue_report import get_revenue_report, calculate_revenue

## ────────────── Логгер и роутер для aiogram ──────────────
router = Router()
logger = logging.getLogger(__name__)

BAR_SPECIAL_ARTICLES = {
    "Комплимент извинение",
    "Ошибка повара",
    "Списание бар порча",
    "Списание бар пролив",
}

KITCHEN_SPECIAL_ARTICLES = {
    "Комплимент извинение",
    "Ошибка повара",
    "Списание кухня порча",
}



## ────────────── Состояния FSM для отчёта по списаниям ──────────────
class WriteoffStates(StatesGroup):
    selecting_start = State()
    selecting_end = State()

## ────────────── Функция формирования и отправки отчёта ──────────────
async def send_grouped_writeoff_report(message: Message, from_dt: datetime, to_dt: datetime):
    try:
        new_status_count = 0
        token = await get_auth_token()
        base_url = get_base_url()
        url = f"{base_url}/resto/api/v2/documents/writeoff"

        params = {
            "dateFrom": from_dt.strftime("%Y-%m-%d"),
            "dateTo": to_dt.strftime("%Y-%m-%d")
        }
        headers = {
            "Cookie": f"key={token}"
        }

        async with httpx.AsyncClient(verify=False, timeout=30) as client:
            response = await client.get(url, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        documents = data.get("response", [])

        await preload_stores()
        async with async_session() as session:
            result = await session.execute(select(Accounts.id, Accounts.name))
            account_map = {row.id: row.name for row in result.all()}

        store_id_to_name = {store_id: name for name, store_id in STORE_CACHE.items()}
        store_ids_in_docs = {
            doc.get("storeId")
            for doc in documents
            if doc.get("storeId")
        }
        missing_store_ids = {
            store_id
            for store_id in store_ids_in_docs
            if store_id not in store_id_to_name
        }

        if missing_store_ids:
            async with async_session() as session:
                rows = await session.execute(
                    select(Store.id, Store.name).where(Store.id.in_(missing_store_ids))
                )
                for store_id, store_name in rows.all():
                    store_id_to_name[store_id] = store_name

        def _detect_segment(value: str | None) -> str | None:
            if not value:
                return None
            normalized = value.lower()
            if "бар" in normalized:
                return "bar"
            if "кухн" in normalized or "пицц" in normalized:
                return "kitchen"
            return None

        grouped = {}
        special_totals = {"bar": 0.0, "kitchen": 0.0}
        for doc in documents:
            if doc.get("status") == "NEW":
                new_status_count += 1
            store_id = doc.get("storeId")
            acc_id = doc.get("accountId")
            store_name = store_id_to_name.get(store_id)
            if not store_name:
                store_name = (
                    (doc.get("store") or {}).get("name")
                    or doc.get("storeName")
                    or "❓ Неизвестно"
                )
            if store_name == "❓ Неизвестно" and store_id:
                store_name = f"❓ Склад {store_id[:8]}"
            acc_name = account_map.get(acc_id, "❓ Неизвестно")

            grouped.setdefault(store_name, {})
            grouped[store_name].setdefault(acc_name, {
                "count": 0,
                "total_cost": 0,
                "total_items": 0
            })

            items = doc.get("items", [])
            total_cost = sum(item.get("cost", 0) or 0 for item in items)

            grouped[store_name][acc_name]["count"] += 1
            grouped[store_name][acc_name]["total_cost"] += total_cost
            grouped[store_name][acc_name]["total_items"] += len(items)

            segment = _detect_segment(store_name)
            if segment == "bar" and acc_name in BAR_SPECIAL_ARTICLES:
                special_totals["bar"] += total_cost
            elif segment == "kitchen" and acc_name in KITCHEN_SPECIAL_ARTICLES:
                special_totals["kitchen"] += total_cost

        lines = []
        overall = {"count": 0, "total_items": 0, "total_cost": 0.0}

        for store_name in sorted(grouped.keys()):
            accounts = grouped[store_name]
            store_totals = {"count": 0, "total_items": 0, "total_cost": 0.0}
            lines.append(f"<b>🏬 {store_name}</b>")
            for acc_name in sorted(accounts.keys()):
                stats = accounts[acc_name]
                lines.append(
                    f"▪️ <i>{acc_name}</i>: {stats['count']} акт(ов), {stats['total_items']} поз., {stats['total_cost']:.2f} ₽"
                )
                for key in store_totals:
                    store_totals[key] += stats[key]
            lines.append(
                f"▫️ <b>Итого по складу:</b> {store_totals['count']} акт(ов), {store_totals['total_items']} поз., {store_totals['total_cost']:.2f} ₽"
            )
            lines.append("")

            for key in overall:
                overall[key] += store_totals[key]

        if overall["count"]:
            lines.append(
                f"📊 <b>Всего списаний:</b> {overall['count']} акт(ов), {overall['total_items']} поз., {overall['total_cost']:.2f} ₽"
            )
            lines.append("")

        date_from_str = from_dt.strftime("%Y-%m-%d")
        date_to_str = to_dt.strftime("%Y-%m-%d")

        def _fmt_currency(amount: float) -> str:
            return f"{amount:,.2f} ₽".replace(",", " ")

        try:
            revenue_raw = await get_revenue_report(date_from_str, date_to_str)
            revenue_data = await calculate_revenue(revenue_raw, date_from_str, date_to_str)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось получить выручку для сравнения со списаниями: %s", exc)
            revenue_data = None

        if revenue_data:
            bar_revenue = revenue_data.get("bar_revenue", 0.0)
            kitchen_revenue = revenue_data.get("kitchen_revenue", 0.0)
            delivery_revenue = revenue_data.get("delivery_revenue", 0.0)

            kitchen_total_revenue = kitchen_revenue + delivery_revenue

            def _safe_percent(numerator: float, denominator: float) -> float | None:
                if not denominator:
                    return None
                return numerator / denominator * 100

            bar_percent = _safe_percent(special_totals["bar"], bar_revenue)
            kitchen_percent = _safe_percent(special_totals["kitchen"], kitchen_total_revenue)

            lines.append("<b>📏 Списания относительно выручки</b>")
            if bar_percent is not None:
                lines.append(
                    f"• Бар: {_fmt_currency(special_totals['bar'])} (" +
                    f"{bar_percent:.2f}% от {_fmt_currency(bar_revenue)})"
                )
            else:
                lines.append("• Бар: нет данных для расчёта")

            if kitchen_percent is not None:
                lines.append(
                    f"• Кухня + доставка: {_fmt_currency(special_totals['kitchen'])} (" +
                    f"{kitchen_percent:.2f}% от {_fmt_currency(kitchen_total_revenue)})"
                )
            else:
                lines.append("• Кухня + доставка: нет данных для расчёта")
            lines.append("")

        if new_status_count > 0:
            lines.append(f"⚠️ <b>Непроведённых актов: {new_status_count}</b>")
        else:
            lines.append("✅ Все акты проведены.")

        final_text = "\n".join(lines)
        await message.answer(f"<b>📉 Сводка списаний</b>\n\n{final_text}", parse_mode="HTML")

    except Exception as e:
        logger.exception("[Ошибка] %s", e)
        await message.answer("❌ Ошибка при получении или обработке данных.")

## ────────────── Старт выбора периода для отчёта ──────────────
@router.message(F.text == "📉 Списания")
async def writeoff_select_date_start(message: Message, state: FSMContext):
    today = datetime.today()
    calendar = build_calendar(today.year, today.month, calendar_id="writeoff", mode="range")
    await state.set_state(WriteoffStates.selecting_start)
    await message.answer("Выберите дату начала периода:", reply_markup=calendar)


## ────────────── Обработка inline-календаря для выбора дат ──────────────
@router.callback_query(F.data.startswith("CAL:writeoff"))
async def handle_writeoff_calendar(callback: CallbackQuery, state: FSMContext):
    data = parse_callback_data(callback.data)
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
        calendar = build_calendar(new_year, new_month, calendar_id="writeoff", mode="range")
        await callback.message.edit_reply_markup(reply_markup=calendar)
        return

    if data["action"] == "DATE":
        selected_date = data["date"]
        state_data = await state.get_data()

        if "from_date" not in state_data:
            await state.update_data(from_date=selected_date.isoformat())
            await state.set_state(WriteoffStates.selecting_end)
            today = datetime.today()
            calendar = build_calendar(today.year, today.month, calendar_id="writeoff", mode="range")
            await callback.message.edit_text("Теперь выберите дату окончания периода:", reply_markup=calendar)
        else:
            from_date = datetime.fromisoformat(state_data["from_date"]).date()
            to_date = selected_date
            from_dt, to_dt = sorted([from_date, to_date])

            await callback.message.edit_text("⏳ Формирую отчёт...")

            await send_grouped_writeoff_report(callback.message, from_dt, to_dt)
            await state.clear()