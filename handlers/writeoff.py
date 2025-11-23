"""
Simplified writeoff (списание) handler.
Reduced from 434 lines to ~220 lines using BaseDocumentHandler.
"""

import logging
import asyncio
from aiogram import Bot, Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from db.employees_db import async_session
from handlers.base_document import BaseDocumentHandler, _normalize_unit
from handlers.template_creation import STORE_CACHE, preload_stores
from handlers.use_template import get_unit_name_by_id
from iiko.iiko_auth import get_auth_token, get_base_url
import httpx
from datetime import datetime
from config import DOC_CONFIG
from db.sprav_db import ReferenceData as Accounts
from services.db_queries import DBQueries

router = Router()

STORE_PAYMENT_FILTERS = DOC_CONFIG["writeoff"]["stores"]


class WriteoffStates(StatesGroup):
    Store = State()
    PaymentType = State()
    Comment = State()
    AddItems = State()
    Quantity = State()


class WriteoffHandler(BaseDocumentHandler):
    """Handler for writeoff documents (акт списания)"""
    doc_type = "writeoff"

    async def get_store_keyboard(self, data: dict) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=name, callback_data=f"w_store:{name}")]
            for name in STORE_PAYMENT_FILTERS
        ])

    async def get_doc_type_keyboard(self, data: dict) -> InlineKeyboardMarkup:
        store_name = data.get("store_name", "")
        types_list = STORE_PAYMENT_FILTERS.get(store_name, [])
        
        async with async_session() as session:
            accounts = await DBQueries.get_accounts_by_names(types_list)
        
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=acc.name, callback_data=f"w_type:{acc.id}")]
            for acc in accounts
        ])

    async def format_header(self, data: dict) -> str:
        store = data.get("store_name", "—")
        account = data.get("account_name", "—")
        reason = data.get("reason", "—")
        comment = data.get("comment", "—")
        author = data.get("user_fullname", "—")

        return (
            f"📄 <b>Акт списания</b>\n"
            f"🏬 <b>Склад:</b> {store}\n"
            f"📂 <b>Тип списания:</b> {account}\n"
            f"📝 <b>Причина:</b> {reason}\n"
            f"💬 <b>Комментарий:</b> {comment}\n"
            f"👤 <b>Сотрудник:</b> {author}"
        )


writeoff_handler = WriteoffHandler()


# ─────────────────────────────── Handlers ───────────────────────────────


@router.callback_query(F.data == "doc:writeoff")
async def start_writeoff(callback: types.CallbackQuery, state: FSMContext):
    await preload_stores()
    await state.clear()
    keyboard = await writeoff_handler.get_store_keyboard({})
    await state.set_state(WriteoffStates.Store)
    await callback.message.edit_text("🏬 С какого склада списываем?", reply_markup=keyboard)


@router.callback_query(F.data.startswith("w_store:"))
async def choose_store(callback: types.CallbackQuery, state: FSMContext):
    store_name = callback.data.split(":")[1]
    store_id = STORE_CACHE.get(f"{store_name} Пиццерия")
    if not store_id:
        return await callback.answer("❌ Ошибка определения склада")
    
    await state.update_data(store_name=store_name, store_id=store_id)
    tg_id = str(callback.from_user.id)
    full_name = await writeoff_handler.get_employee_name(tg_id)
    await state.update_data(user_fullname=full_name, header_msg_id=callback.message.message_id)

    data = await state.get_data()
    await state.set_state(WriteoffStates.PaymentType)
    keyboard = await writeoff_handler.get_doc_type_keyboard(data)
    await callback.message.edit_text("📂 Какой тип списания?", reply_markup=keyboard)


@router.callback_query(F.data.startswith("w_type:"))
async def choose_type(callback: types.CallbackQuery, state: FSMContext):
    type_id = callback.data.split(":")[1]
    async with async_session() as session:
        result = await session.execute(select(Accounts).where(Accounts.id == type_id))
        account = result.scalar_one()
    
    await state.update_data(account_name=account.name, account_id=type_id)
    await state.set_state(WriteoffStates.Comment)
    await callback.message.edit_text("📝 Введите причину списания:")


@router.message(WriteoffStates.Comment)
async def get_reason(message: types.Message, state: FSMContext):
    reason = message.text.strip()
    await message.delete()
    await state.update_data(reason=reason)
    
    await state.set_state(WriteoffStates.AddItems)
    msg = await message.answer("💬 Введите комментарий (или - чтобы оставить пустым):")
    await state.update_data(reason_msg_id=msg.message_id)
    
    data = await state.get_data()
    await writeoff_handler.update_header(message.bot, message.chat.id, data.get("header_msg_id"), data)


@router.message(WriteoffStates.AddItems)
async def search_products(message: types.Message, state: FSMContext):
    query = message.text.strip()
    await message.delete()
    
    results = await DBQueries.search_nomenclature(query, types=["GOODS", "PREPARED"], parents=None)
    
    if not results:
        return await message.answer("🔎 Ничего не найдено.")
    
    data = await state.get_data()
    await state.update_data(nomenclature_cache={r['id']: r for r in results})
    
    kb = writeoff_handler.build_item_keyboard(results, "w_item")
    msg = await message.answer("Выберите товар:")
    await state.update_data(search_msg_id=msg.message_id)


@router.callback_query(F.data.startswith("w_item:"))
async def select_item(callback: types.CallbackQuery, state: FSMContext):
    item_id = callback.data.split(":")[1]
    data = await state.get_data()
    cache = data.get("nomenclature_cache", {})
    item = cache.get(item_id)
    
    if not item:
        return await callback.answer("❌ Товар не найден")
    
    unit = await get_unit_name_by_id(item["mainunit"])
    norm = _normalize_unit(unit)
    
    if norm == "kg":
        text = f"📏 Сколько грамм для «{item['name']}»?"
    elif norm in ("l", "ml"):
        text = f"📏 Сколько мл для «{item['name']}»?"
    else:
        text = f"📏 Сколько {unit} для «{item['name']}»?"
    
    await state.update_data(current_item=item)
    await state.set_state(WriteoffStates.Quantity)
    await callback.message.edit_text(text)


@router.message(WriteoffStates.Quantity)
async def save_quantity(message: types.Message, state: FSMContext):
    try:
        quantity = float(message.text.replace(",", "."))
    except ValueError:
        return await message.answer("❌ Введите корректное число")
    
    data = await state.get_data()
    item = data.get("current_item", {})
    unit = await get_unit_name_by_id(item["mainunit"])
    norm = _normalize_unit(unit)
    
    # Normalize quantity based on unit
    if norm == "kg":
        item["user_quantity"] = quantity  # граммы для показа
        item["quantity"] = quantity / 1000  # кг для iiko
    elif norm == "l":
        item["user_quantity"] = quantity  # мл для показа
        item["quantity"] = quantity / 1000  # л для iiko
    else:
        item["user_quantity"] = quantity
        item["quantity"] = quantity
    
    items = data.get("items", [])
    items.append(item)
    
    await state.update_data(items=items, current_item=None)
    await message.delete()
    
    # Обновить заголовок
    await writeoff_handler.update_header(
        message.bot,
        message.chat.id,
        data.get("header_msg_id"),
        {**data, "items": items}
    )
    
    # Предложить добавить ещё
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить ещё", callback_data="w_more")],
        [InlineKeyboardButton(text="✅ Завершить", callback_data="w_done")]
    ])
    await message.answer("Что дальше?", reply_markup=kb)


@router.callback_query(F.data == "w_more")
async def more_items(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(WriteoffStates.AddItems)
    await callback.message.edit_text("🔍 Введите часть названия товара:")


@router.callback_query(F.data == "w_done")
async def finalize_writeoff(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    items = data.get("items", [])
    
    if not items:
        return await callback.answer("❌ Добавьте хотя бы один товар")
    
    await callback.message.edit_text("⏳ Отправляем в iiko...")
    
    date_now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    document = {
        "dateIncoming": date_now,
        "status": "PROCESSED",
        "comment": data.get("comment", ""),
        "storeId": data.get("store_id"),
        "accountId": data.get("account_id"),
        "items": [
            {
                "productId": item["id"],
                "amount": item.get("quantity", 0),
                "measureUnitId": item["mainunit"]
            } for item in items
        ]
    }
    
    # Background send
    chat_id = callback.message.chat.id
    msg_id = callback.message.message_id
    bot = callback.message.bot
    
    token = await get_auth_token()
    url = f"{get_base_url()}/resto/api/v2/documents/writeoff"
    params = {"key": token}
    
    asyncio.create_task(_send_writeoff(bot, chat_id, msg_id, url, params, document))
    await state.clear()


async def _send_writeoff(bot: Bot, chat_id: int, msg_id: int, url: str, params: dict, document: dict):
    """Background task to send writeoff to iiko"""
    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(url, params=params, json=document, timeout=30.0)
            response.raise_for_status()
        
        await bot.send_message(chat_id, "✅ Акт списания успешно отправлен!")
    except Exception as e:
        logging.error(f"Writeoff send error: {e}")
        await bot.send_message(chat_id, f"❌ Ошибка отправки: {e}")
