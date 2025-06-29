import logging
from aiogram import Bot, Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from db.employees_db import async_session, Employee
from handlers.template_creation import STORE_CACHE, preload_stores, search_nomenclature, Nomenclature, get_store_id_by_name
from handlers.use_template import get_unit_name_by_id
from iiko.iiko_auth import get_auth_token, get_base_url
from html import escape
import httpx
from datetime import datetime

router = Router()

class InternalTransferStates(StatesGroup):
    StoreFrom = State()
    StoreTo = State()
    Comment = State()
    AddItems = State()
    Quantity = State()

async def update_transfer_header(bot: Bot, chat_id: int, msg_id: int, data: dict):
    from_store = data.get("store_from_name", "—")
    to_store = data.get("store_to_name", "—")
    comment = data.get("comment", "—")
    author = data.get("user_fullname", "—")
    items = data.get("items", [])

    text = (
        f"🔄 <b>Внутреннее перемещение</b>\n"
        f"🏬 <b>Откуда:</b> {from_store}\n"
        f"🏬 <b>Куда:</b> {to_store}\n"
        f"💬 <b>Комментарий:</b> {comment}\n"
        f"👤 <b>Сотрудник:</b> {author}"
    )

    if items:
        text += "\n<b>Товары:</b>\n"
        for i, item in enumerate(items, 1):
            unit = await get_unit_name_by_id(item['mainunit'])
            value = item.get("user_quantity", "—")
            if unit.lower() in ["кг", "kg", "килограмм"]:
                text += f"{i}. {item['name']} — <b>{value} г</b>\n"
            else:
                text += f"{i}. {item['name']} — <b>{value} {unit}</b>\n"

    try:
        await bot.edit_message_text(
            chat_id=chat_id,
            message_id=msg_id,
            text=text,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.warning(f"⚠️ Не удалось обновить заголовок: {e}")

@router.callback_query(F.data == "doc:move")
async def start_internal_transfer(callback: types.CallbackQuery, state: FSMContext):
    await preload_stores()
    await state.clear()
    ALLOWED_STORES = ["Бар", "Кухня", "Кондитерский"]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"t_store_from:{name}")]
        for name in ALLOWED_STORES
    ])
    await state.set_state(InternalTransferStates.StoreFrom)
    await callback.message.edit_text("🏬 С какого склада перемещаем?", reply_markup=keyboard)

@router.callback_query(F.data.startswith("t_store_from:"))
async def choose_store_from(callback: types.CallbackQuery, state: FSMContext):
    store_from_name = callback.data.split(":")[1]
    store_from_id = await get_store_id_by_name(store_from_name)
    if not store_from_id:
        return await callback.answer("❌ Ошибка определения склада")
    await state.update_data(store_from_name=store_from_name, store_from_id=store_from_id)

    ALLOWED_STORES = ["Бар", "Кухня", "Кондитерский"]
    to_stores = [name for name in ALLOWED_STORES if name != store_from_name]
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=name, callback_data=f"t_store_to:{name}")]
                        for name in to_stores]
    )
    await state.set_state(InternalTransferStates.StoreTo)
    await state.update_data(header_msg_id=callback.message.message_id)
    # Имя пользователя
    tg_id = str(callback.from_user.id)
    async with async_session() as session:
        result = await session.execute(select(Employee).where(Employee.telegram_id == tg_id))
        user = result.scalar_one_or_none()
        full_name = f"{user.first_name} {user.last_name}" if user else "Неизвестно"
    await state.update_data(user_fullname=full_name)
    await callback.message.answer("🏬 На какой склад перемещаем?", reply_markup=keyboard)

@router.callback_query(F.data.startswith("t_store_to:"))
async def choose_store_to(callback: types.CallbackQuery, state: FSMContext):
    store_to_name = callback.data.split(":")[1]
    store_to_id = await get_store_id_by_name(store_to_name)
    if not store_to_id:
        return await callback.answer("❌ Ошибка определения склада")
    await state.update_data(store_to_name=store_to_name, store_to_id=store_to_id)
    await state.set_state(InternalTransferStates.Comment)
    await callback.message.edit_text("💬 Введите комментарий к перемещению (или - чтобы оставить пустым):")

    # Обновить шапку перемещения
    data = await state.get_data()
    await update_transfer_header(callback.message.bot, callback.message.chat.id, data["header_msg_id"], data)

@router.message(InternalTransferStates.Comment)
async def get_comment(message: types.Message, state: FSMContext):
    comment = message.text.strip()
    await message.delete()
    await state.update_data(comment=comment if comment != "-" else "", items=[])
    await state.set_state(InternalTransferStates.AddItems)
    data = await state.get_data()
    msg = await message.answer("🔍 Введите часть названия товара для перемещения:")
    await state.update_data(search_msg_id=msg.message_id)
    await update_transfer_header(message.bot, message.chat.id, data["header_msg_id"], data)

@router.message(InternalTransferStates.AddItems)
async def search_products(message: types.Message, state: FSMContext):
    query = message.text.strip()
    await message.delete()
    async with async_session() as session:
        terms = [t.strip() for t in query.lower().split() if t.strip()]
        if not terms:
            return await message.answer("🔎 Введите часть названия товара.")
        q = select(Nomenclature.id, Nomenclature.name, Nomenclature.mainunit).limit(50)
        q = q.where(Nomenclature.type == "GOODS")  # ← только товары!
        for term in terms:
            q = q.where(Nomenclature.name.ilike(f"%{term}%"))
        result = await session.execute(q)
        rows = result.all()
        results = [{"id": r.id, "name": r.name, "mainunit": r.mainunit} for r in rows]
    if not results:
        return await message.answer("🔎 Ничего не найдено.")
    data = await state.get_data()
    await state.update_data(nomenclature_cache={r['id']: r for r in results})
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=r['name'], callback_data=f"t_item:{r['id']}")]
        for r in results
    ])
    msg_id = data.get("search_msg_id")
    if msg_id:
        await message.bot.edit_message_text(
            "Выберите товар:",
            chat_id=message.chat.id,
            message_id=msg_id,
            reply_markup=kb
        )

@router.callback_query(F.data.startswith("t_item:"))
async def ask_quantity(callback: types.CallbackQuery, state: FSMContext):
    item_id = callback.data.split(":")[1]
    data = await state.get_data()
    item = data.get("nomenclature_cache", {}).get(item_id)
    await state.update_data(current_item=item)
    unit = await get_unit_name_by_id(item["mainunit"])
    # Если кг — спрашиваем граммы, иначе — количество
    if unit.lower() in ["кг", "kg", "килограмм"]:
        text = f"📏 🖊 Сколько грамм для «{item['name']}»?"
    else:
        text = f"📏 🖊 Сколько {unit} для «{item['name']}»?"
    await state.set_state(InternalTransferStates.Quantity)
    await callback.message.edit_text(text)
    await state.update_data(quantity_msg_id=callback.message.message_id)

@router.message(InternalTransferStates.Quantity)
async def save_quantity(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        qty = float(message.text.replace(",", "."))
        item = data["current_item"]
        unit = await get_unit_name_by_id(item["mainunit"])
        if unit.lower() in ["кг", "kg", "килограмм"]:
            item["user_quantity"] = qty  # граммы для UI
            item["quantity"] = qty / 1000  # кг для iiko
        else:
            item["user_quantity"] = qty
            item["quantity"] = qty
        items = data["items"]
        items.append(item)
        await state.update_data(items=items)
        await message.delete()
    except:
        return await message.answer("⚠️ Введите корректное число")

    await state.set_state(InternalTransferStates.AddItems)
    msg_id = data.get("quantity_msg_id")
    if msg_id:
        await message.bot.edit_message_text(
            "🔍 Введите часть названия следующего товара или нажмите «Готово»:",
            chat_id=message.chat.id,
            message_id=msg_id,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Готово", callback_data="t_done")]
            ])
        )
        header_id = data.get("header_msg_id")
        if header_id:
            new_data = await state.get_data()
            await update_transfer_header(message.bot, message.chat.id, header_id, new_data)

@router.callback_query(F.data == "t_done")
async def finalize_transfer(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    date_now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    document = {
        "dateIncoming": date_now,
        "status": "NEW",
        "comment": data["comment"],
        "storeFromId": data["store_from_id"],
        "storeToId": data["store_to_id"],
        "items": [
            {
                "productId": item["id"],
                "amount": item["quantity"],
                "measureUnitId": item["mainunit"]
            } for item in data["items"]
        ]
    }
    print("📦 Финальный JSON-документ для iiko:")
    print(document)

    token = await get_auth_token()
    url = f"{get_base_url()}/resto/api/v2/documents/internalTransfer"
    headers = {"Content-Type": "application/json"}
    params = {"key": token}

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, params=params, json=document)
            response.raise_for_status()
            await callback.message.edit_text("✅ Внутреннее перемещение успешно отправлено в iiko.")
        except httpx.HTTPError as e:
            text = f"❌ Ошибка: {e.response.status_code}\n{e.response.text}"
            await callback.message.edit_text(f"<pre>{escape(text)}</pre>", parse_mode="HTML")
    await state.clear()
