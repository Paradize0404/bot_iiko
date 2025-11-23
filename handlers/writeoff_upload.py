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

## ────────────── Логгер и роутер для aiogram ──────────────
router = Router()
logger = logging.getLogger(__name__)



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

        grouped = {}
        for doc in documents:
            if doc.get("status") == "NEW":
                new_status_count += 1
            store_id = doc.get("storeId")
            acc_id = doc.get("accountId")
            store_name = next((name for name, id_ in STORE_CACHE.items() if id_ == store_id), "❓ Неизвестно")
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

        lines = []
        for store_name, accounts in grouped.items():
            lines.append(f"<b>🏬 {store_name}</b>")
            for acc_name, stats in accounts.items():
                lines.append(f"▪️ <i>{acc_name}</i>: {stats['count']} акт(ов), {stats['total_items']} поз., {stats['total_cost']:.2f} ₽")
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