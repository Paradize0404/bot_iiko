from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from db.employees_db import async_session
from handlers.template_creation import STORE_CACHE, preload_stores, search_nomenclature
from handlers.use_template import get_unit_name_by_id
from iiko.iiko_auth import get_auth_token, get_base_url
from html import escape
import httpx
from datetime import datetime

router = Router()

# FSM Состояния для акта списания
class WriteoffStates(StatesGroup):
    Store = State()
    PaymentType = State()
    Comment = State()
    AddItems = State()
    Quantity = State()

# Кешируем склад -> допустимые типы списания
STORE_PAYMENT_FILTERS = {
    "Бар": ["Списание бар порча", "Списание бар пролив", "Списание бар проработка"],
    "Кухня": ["Списание кухня порча", "Списание кухня проработка", "Питание персонал"],
    "Кондитерский": ["Списание кондитерский порча"]
}

@router.callback_query(F.data == "doc:writeoff")
async def start_writeoff(callback: types.CallbackQuery, state: FSMContext):
    await preload_stores()
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"w_store:{name}")]
        for name in STORE_PAYMENT_FILTERS
    ])
    await state.set_state(WriteoffStates.Store)
    await callback.message.edit_text("🏬 С какого склада списываем?", reply_markup=keyboard)

@router.callback_query(F.data.startswith("w_store:"))
async def choose_store(callback: types.CallbackQuery, state: FSMContext):
    store_name = callback.data.split(":")[1]
    store_id = STORE_CACHE.get(f"{store_name} Пиццерия")
    if not store_id:
        return await callback.answer("❌ Ошибка определения склада")
    await state.update_data(store_name=store_name, store_id=store_id)

    async with async_session() as session:
        result = await session.execute(
            select().where(F.ReferenceData.root_type == "PaymentType")
        )
        all_payment_types = result.scalars().all()
        names = STORE_PAYMENT_FILTERS[store_name]
        filtered = [pt for pt in all_payment_types if pt.name in names]

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=pt.name, callback_data=f"w_type:{pt.id}:{pt.name}")]
                             for pt in filtered]
        )
        await state.set_state(WriteoffStates.PaymentType)
        await callback.message.edit_text("📂 Какой тип списания?", reply_markup=keyboard)

@router.callback_query(F.data.startswith("w_type:"))
async def choose_type(callback: types.CallbackQuery, state: FSMContext):
    _, type_id, type_name = callback.data.split(":", 2)
    await state.update_data(payment_type_id=type_id, payment_type_name=type_name)
    await state.set_state(WriteoffStates.Comment)
    await callback.message.edit_text("✍️ Укажи причину списания:")

@router.message(WriteoffStates.Comment)
async def get_comment(message: types.Message, state: FSMContext):
    comment = message.text.strip()
    await message.delete()
    tg_id = message.from_user.id
    async with async_session() as session:
        result = await session.execute(select().where(F.Employees.telegram_id == str(tg_id)))
        user = result.scalar_one_or_none()
        name = user.name if user else "Неизвестно"
    await state.update_data(comment=comment + f" | Ввел: {name}", items=[])
    await state.set_state(WriteoffStates.AddItems)
    await message.answer("🔍 Введите часть названия товара:")

@router.message(WriteoffStates.AddItems)
async def search_products(message: types.Message, state: FSMContext):
    query = message.text.strip()
    await message.delete()
    results = await search_nomenclature(query)
    if not results:
        return await message.answer("🔎 Ничего не найдено.")
    await state.update_data(nomenclature_cache={r['id']: r for r in results})
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=r['name'], callback_data=f"w_item:{r['id']}")]
        for r in results
    ])
    await message.answer("Выберите товар:", reply_markup=kb)

@router.callback_query(F.data.startswith("w_item:"))
async def ask_quantity(callback: types.CallbackQuery, state: FSMContext):
    item_id = callback.data.split(":")[1]
    data = await state.get_data()
    item = data.get("nomenclature_cache", {}).get(item_id)
    await state.update_data(current_item=item)
    unit = await get_unit_name_by_id(item["mainunit"])
    await state.set_state(WriteoffStates.Quantity)
    await callback.message.answer(f"📏 Сколько {unit} для «{item['name']}»?")

@router.message(WriteoffStates.Quantity)
async def save_quantity(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        qty = float(message.text.replace(",", "."))
        item = data["current_item"]
        item["quantity"] = qty
        items = data["items"]
        items.append(item)
        await state.update_data(items=items)
    except:
        return await message.answer("⚠️ Введите корректное число")
    await state.set_state(WriteoffStates.AddItems)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить еще", callback_data="w_more")],
        [InlineKeyboardButton(text="✅ Готово", callback_data="w_done")]
    ])
    await message.answer("Добавлено. Что дальше?", reply_markup=kb)
    await message.delete()

@router.callback_query(F.data == "w_more")
async def more_items(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(WriteoffStates.AddItems)
    await callback.message.edit_text("🔍 Введите часть названия товара:")

@router.callback_query(F.data == "w_done")
async def finalize_writeoff(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    date_now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    document = {
        "dateIncoming": date_now,
        "status": "NEW",
        "comment": data["comment"],
        "storeId": data["store_id"],
        "accountId": data["payment_type_id"],
        "items": [
            {
                "productId": item["id"],
                "amount": item["quantity"],
                "measureUnitId": item["mainunit"]
            } for item in data["items"]
        ]
    }

    token = await get_auth_token()
    url = f"{get_base_url()}/resto/api/v2/documents/writeoff"
    headers = {"Content-Type": "application/json"}
    params = {"key": token}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, params=params, json=document)
            response.raise_for_status()
            await callback.message.edit_text("✅ Акт списания успешно отправлен в iiko.")
        except httpx.HTTPError as e:
            text = f"❌ Ошибка: {e.response.status_code}\n{e.response.text}"
            await callback.message.edit_text(f"<pre>{escape(text)}</pre>", parse_mode="HTML")
    await state.clear()
