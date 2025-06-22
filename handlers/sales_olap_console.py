import logging
import asyncio
import httpx
import pandas as pd
from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from iiko.iiko_auth import get_auth_token, get_base_url
import xml.etree.ElementTree as ET
from decimal import Decimal
from aiogram.fsm.state import StatesGroup, State
from datetime import datetime


from keyboards.inline_calendar import build_calendar, parse_callback_data
router = Router()


class SalesReportStates(StatesGroup):
    selecting_start = State()
    selecting_end = State()


logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("sales_olap_console.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


def _auto_cast(text):
    if text is None:
        return None
    try:
        return int(text)
    except Exception:
        try:
            return Decimal(text)
        except Exception:
            return text.strip()


def parse_xml_report(xml: str):
    root = ET.fromstring(xml)
    rows = []
    for row in root.findall("./r"):
        rows.append({child.tag: _auto_cast(child.text) for child in row})
    return rows


async def get_olap_report(
        report="SALES",
        date_from=None,
        date_to=None,
        group_rows=None,
        group_cols=None,
        agr_fields=None,
        summary=True
    ):
    token = await get_auth_token()
    base_url = get_base_url()
    params = [
        ("key", token),
        ("report", report),
        ("from", date_from),  # переменная date_from уже строка
        ("to", date_to),      # переменная date_to уже строка
        ("summary", "true" if summary else "false"),
        ("OrderDeleted", ""),
        ("DeletedWithWriteoff", ""),
    ]
    for f in group_rows or []:
        params.append(("groupRow", f))
    for f in group_cols or []:
        params.append(("groupCol", f))
    for f in agr_fields or []:
        params.append(("agr", f))
    async with httpx.AsyncClient(base_url=base_url, timeout=60) as client:
        r = await client.get("/resto/api/reports/olap", params=params)
        ct = r.headers.get("content-type", "")
        if ct.startswith("application/json"):
            return r.json()["rows"]
        elif ct.startswith("application/xml") or ct.startswith("text/xml"):
            return parse_xml_report(r.text)
        else:
            print("Неожиданный Content-Type:", ct)
            print(r.text[:500])
            raise RuntimeError("Неизвестный формат ответа")


def get_not_deleted(df):
    """Возвращает DataFrame только с не удалёнными позициями"""
    return df[
        (df["DeletedWithWriteoff"] == "NOT_DELETED") &
        (df["OrderDeleted"] == "NOT_DELETED")
    ].copy()


def get_main_report(filtered_df) -> str:
    # Категории
    is_yandex = filtered_df["PayTypes.Combo"].str.strip().str.lower() == "яндекс.оплата"
    is_personal = filtered_df["DishCategory"] == "Персонал"
    is_mod = filtered_df["DishCategory"] == "Модификаторы"
    is_no_pay = filtered_df["PayTypes.Combo"].str.strip() == "(без оплаты)"

    main_revenue_df = filtered_df[~(is_personal | is_mod | is_no_pay | is_yandex)]
    bar_revenue = main_revenue_df[main_revenue_df["CookingPlace"] == "Бар"]["DishDiscountSumInt"].sum()
    kitchen_revenue = main_revenue_df[
        main_revenue_df["CookingPlace"].isin(["Кухня", "Кухня-Пицца", "Пицца"])
    ]["DishDiscountSumInt"].sum()
    yandex_revenue = filtered_df[is_yandex]["DishSumInt"].sum()

    cost_df = filtered_df[~(is_personal | is_mod | is_no_pay)]
    bar_cost = cost_df[cost_df["CookingPlace"] == "Бар"]["ProductCostBase.ProductCost"].sum()
    kitchen_cost = cost_df[
        cost_df["CookingPlace"].isin(["Кухня", "Кухня-Пицца", "Пицца"])
    ]["ProductCostBase.ProductCost"].sum()

    mod_cost = filtered_df[is_mod]["ProductCostBase.ProductCost"].sum()
    pers_cost = filtered_df[is_personal]["ProductCostBase.ProductCost"].sum()
    pers_revenue = filtered_df[is_personal]["DishDiscountSumInt"].sum()

    text = (
        "🍕 *ОТЧЁТ ПО ВЫРУЧКЕ И СЕБЕСТОИМОСТИ*\n\n"
        "💵 *ВЫРУЧКА:*\n"
        f"• Бар: {bar_revenue:,.0f} ₽\n"
        f"• Кухня (включая пиццу): {kitchen_revenue:,.0f} ₽\n"
        f"• Яндекс.Оплата: {yandex_revenue:,.0f} ₽\n\n"
        "🧾 *СЕБЕСТОИМОСТЬ:*\n"
        f"• Бар: {bar_cost:,.0f} ₽\n"
        f"• Кухня (включая пиццу): {kitchen_cost:,.0f} ₽\n\n"
        "👥 *Персонал и модификаторы* _(в расчетах не участвуют)_\n"
        f"• Модификаторы: {mod_cost:,.0f} ₽\n"
        f"• Персонал: {pers_cost:,.0f} ₽ (выручка: {pers_revenue:,.0f} ₽)\n"
    )
    return text


def get_cost_and_revenue_by_category(filtered_df) -> str:
    df = filtered_df[~filtered_df["DishCategory"].isin(["Персонал", "Модификаторы"])]
    summary = (
        df.groupby("DishCategory")[["ProductCostBase.ProductCost", "DishDiscountSumInt"]]
        .sum()
        .reset_index()
    )
    summary["CostPercent"] = (
        summary["ProductCostBase.ProductCost"] / summary["DishDiscountSumInt"] * 100
    ).round(2)

    total_cost = summary["ProductCostBase.ProductCost"].sum()
    total_revenue = summary["DishDiscountSumInt"].sum()
    total_percent = round(total_cost / total_revenue * 100, 2) if total_revenue else 0

    lines = ["📊 *Себестоимость и выручка по категориям*\n"]
    lines.append(f"{'Категория':<22} | {'Себестоимость':>15} | {'Выручка':>12} | {'%':>6}")
    lines.append('-' * 60)
    for _, row in summary.iterrows():
        cat = str(row['DishCategory'])
        cost = row['ProductCostBase.ProductCost']
        revenue = row['DishDiscountSumInt']
        percent = row['CostPercent']
        lines.append(f"{cat:<22} | {cost:>15,.2f} | {revenue:>12,.2f} | {percent:>5.2f}%")
    lines.append('-' * 60)
    lines.append(
        f"*ИТОГО*: {total_cost:,.2f} ₽ | {total_revenue:,.2f} ₽ | {total_percent:.2f}%\n"
        "(Себестоимость | Выручка | % себестоимости)"
    )
    return "\n".join(lines).replace(',', ' ')


# ========== aiogram ROUTES ==========

# Кнопка: 📈 Выручка / Себестоимость
@router.message(F.text == "📈 Выручка / Себестоимость")
async def start_main_report(message: types.Message, state: FSMContext):
    await message.answer("Выберите дату *начала* периода:", reply_markup=build_calendar(
        year=datetime.now().year, month=datetime.now().month, calendar_id="sales_main_start", mode="single"
    ))
    await state.set_state(SalesReportStates.selecting_start)
    await state.update_data(report_type="main")

@router.message(F.text == "📑 Себестоимость по категориям")
async def start_category_report(message: types.Message, state: FSMContext):
    await message.answer("Выберите дату *начала* периода:", reply_markup=build_calendar(
        year=datetime.now().year, month=datetime.now().month, calendar_id="sales_cat_start", mode="single"
    ))
    await state.set_state(SalesReportStates.selecting_start)
    await state.update_data(report_type="category")

@router.callback_query(lambda c: c.data.startswith("CAL:"))
async def calendar_handler(call: types.CallbackQuery, state: FSMContext):
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
        selected_date = data["date"].strftime("%d.%m.%Y")
        user_data = await state.get_data()
        report_type = user_data.get("report_type")

        if cur_state == SalesReportStates.selecting_start.state:
            await state.update_data(date_start=selected_date)
            await state.set_state(SalesReportStates.selecting_end)
            await call.message.edit_text(f"Дата начала: {selected_date}\nТеперь выберите дату *конца* периода:", reply_markup=build_calendar(
                year=data["date"].year, month=data["date"].month, calendar_id="sales_end", mode="single"
            ))
            await call.answer()
            return

        elif cur_state == SalesReportStates.selecting_end.state:
            await state.update_data(date_end=selected_date)
            data_ctx = await state.get_data()
            await state.clear()

            # Запуск генерации отчёта
            msg = await call.message.edit_text("⏳ Формируем отчёт... Пожалуйста, подождите.")

            raw = await get_olap_report(
                date_from=data_ctx["date_start"],
                date_to=data_ctx["date_end"],
                group_rows=[
                    "OpenTime", "CookingPlace",
                    "DeletedWithWriteoff", "OrderDeleted", "DishCategory", "PayTypes.Combo", "OrderNum"
                ],
                agr_fields=["DishDiscountSumInt", "ProductCostBase.ProductCost", "DishSumInt"],
            )
            df = pd.DataFrame(raw)
            filtered_df = get_not_deleted(df)
            if data_ctx["report_type"] == "main":
                text = get_main_report(filtered_df)
            else:
                text = get_cost_and_revenue_by_category(filtered_df)

            await msg.edit_text(text, parse_mode="Markdown")
            await call.answer()
            return

# ========== CONSOLE MAIN ==========
async def main():
    logger.info("Получаем OLAP-отчет по продажам")
    raw = await get_olap_report(
        group_rows=[
            "OpenTime", "CookingPlace",
            "DeletedWithWriteoff", "OrderDeleted", "DishCategory", "PayTypes.Combo", "OrderNum"
        ],
        agr_fields=["DishDiscountSumInt", "ProductCostBase.ProductCost", "DishSumInt"],
    )
    if not raw:
        logger.error("Пустой ответ от iiko")
        print("Пустой ответ от iiko")
        return
    df = pd.DataFrame(raw)
    logger.debug("Данные загружены в DataFrame")
    filtered_df = get_not_deleted(df)
    logger.info(f"После фильтрации осталось {len(filtered_df)} строк")
    print(get_main_report(filtered_df))
    print("\n---\n")
    print(get_cost_and_revenue_by_category(filtered_df))
    logger.info("Скрипт завершил работу успешно.")

if __name__ == "__main__":
    asyncio.run(main())
