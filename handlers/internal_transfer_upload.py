"""
Simplified internal transfer handler using BaseDocumentHandler.
Reduced from 233 lines to ~150 lines.
"""

import logging
import asyncio
from aiogram import Bot, Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select
from handlers.base_document import BaseDocumentHandler, _normalize_unit
from handlers.common import STORE_CACHE, preload_stores, get_store_id_by_name, get_unit_name_by_id
from iiko.iiko_auth import get_auth_token, get_base_url
import httpx
from datetime import datetime
from services.db_queries import DBQueries

router = Router()


class InternalTransferStates(StatesGroup):
    StoreFrom = State()
    StoreTo = State()
    Comment = State()
    AddItems = State()
    Quantity = State()


class TransferHandler(BaseDocumentHandler):
    """Handler for internal transfers (внутреннее перемещение)"""
    doc_type = "transfer"

    async def get_store_keyboard(self, data: dict) -> InlineKeyboardMarkup:
        """Get keyboard for store selection"""
        return InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="Бар", callback_data="t_store_from:Бар")],
            [InlineKeyboardButton(text="Кухня", callback_data="t_store_from:Кухня")]
        ])

    async def get_doc_type_keyboard(self, data: dict) -> InlineKeyboardMarkup:
        """Not used for transfers"""
        return InlineKeyboardMarkup()

    async def format_header(self, data: dict) -> str:
        from_store = data.get("store_from_name", "—")
        to_store = data.get("store_to_name", "—")
        comment = data.get("comment", "—")
        author = data.get("user_fullname", "—")

        return (
            f"🔄 <b>Внутреннее перемещение</b>\n"
            f"🏬 <b>Откуда:</b> {from_store}\n"
            f"🏬 <b>Куда:</b> {to_store}\n"
            f"💬 <b>Комментарий:</b> {comment}\n"
            f"👤 <b>Сотрудник:</b> {author}"
        )


transfer_handler = TransferHandler()


# ─────────────────────────────── Handlers ───────────────────────────────


@router.callback_query(F.data == "doc:move")
async def start_transfer(callback: types.CallbackQuery, state: FSMContext):
    await preload_stores()
    await state.clear()
    keyboard = await transfer_handler.get_store_keyboard({})
    await state.set_state(InternalTransferStates.StoreFrom)
    await callback.message.edit_text("🏬 Откуда перемещаем?", reply_markup=keyboard)


@router.callback_query(F.data.startswith("t_store_from:"))
async def choose_store_from(callback: types.CallbackQuery, state: FSMContext):
    store_name = callback.data.split(":")[1]
    store_id = await get_store_id_by_name(store_name)
    if not store_id:
        return await callback.answer("❌ Ошибка определения склада")
    
    await state.update_data(store_from_name=store_name, store_from_id=store_id)
    await state.set_state(InternalTransferStates.StoreTo)
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Бар", callback_data="t_store_to:Бар")],
        [InlineKeyboardButton(text="Кухня", callback_data="t_store_to:Кухня")]
    ])
    await callback.message.edit_text("🏬 Куда перемещаем?", reply_markup=keyboard)


@router.callback_query(F.data.startswith("t_store_to:"))
async def choose_store_to(callback: types.CallbackQuery, state: FSMContext):
    store_name = callback.data.split(":")[1]
    store_id = await get_store_id_by_name(store_name)
    if not store_id:
        return await callback.answer("❌ Ошибка определения склада")
    
    await state.update_data(store_to_name=store_name, store_to_id=store_id)
    await state.set_state(InternalTransferStates.Comment)
    
    # Get employee name
    tg_id = str(callback.from_user.id)
    full_name = await transfer_handler.get_employee_name(tg_id)
    await state.update_data(user_fullname=full_name, header_msg_id=callback.message.message_id)
    
    await callback.message.edit_text("💬 Введите комментарий к перемещению (или - чтобы оставить пустым):")


@router.message(InternalTransferStates.Comment)
async def get_comment(message: types.Message, state: FSMContext):
    comment = message.text.strip() if message.text != "-" else ""
    await message.delete()
    await state.update_data(comment=comment, items=[])
    await state.set_state(InternalTransferStates.AddItems)
    
    msg = await message.answer("🔍 Введите часть названия товара:")
    await state.update_data(search_msg_id=msg.message_id)
    
    data = await state.get_data()
    await transfer_handler.update_header(message.bot, message.chat.id, data.get("header_msg_id"), data)


@router.message(InternalTransferStates.AddItems)
async def search_products(message: types.Message, state: FSMContext):
    query = message.text.strip()
    await message.delete()
    
    results = await DBQueries.search_nomenclature(query, types=["GOODS"], parents=None)
    
    if not results:
        return await message.answer("🔎 Ничего не найдено.")
    
    data = await state.get_data()
    await state.update_data(nomenclature_cache={r['id']: r for r in results})
    
    kb = transfer_handler.build_item_keyboard(results, "t_item")
    msg = await message.answer("Выберите товар:")
    await state.update_data(search_msg_id=msg.message_id)


@router.callback_query(F.data.startswith("t_item:"))
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
    await state.set_state(InternalTransferStates.Quantity)
    await callback.message.edit_text(text)


@router.message(InternalTransferStates.Quantity)
async def save_quantity(message: types.Message, state: FSMContext):
    try:
        quantity = float(message.text.replace(",", "."))
    except ValueError:
        return await message.answer("❌ Введите корректное число")
    
    data = await state.get_data()
    item = data.get("current_item", {})
    unit = await get_unit_name_by_id(item["mainunit"])
    norm = _normalize_unit(unit)
    
    # Normalize quantity
    if norm == "kg":
        item["user_quantity"] = quantity
        item["quantity"] = quantity / 1000
    elif norm == "l":
        item["user_quantity"] = quantity
        item["quantity"] = quantity / 1000
    else:
        item["user_quantity"] = quantity
        item["quantity"] = quantity
    
    items = data.get("items", [])
    items.append(item)
    
    await state.update_data(items=items, current_item=None)
    await message.delete()
    
    # Update header
    await transfer_handler.update_header(
        message.bot,
        message.chat.id,
        data.get("header_msg_id"),
        {**data, "items": items}
    )
    
    # Ask for more items or finish
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить ещё", callback_data="t_more")],
        [InlineKeyboardButton(text="✅ Завершить", callback_data="t_done")]
    ])
    await message.answer("Что дальше?", reply_markup=kb)


@router.callback_query(F.data == "t_more")
async def more_items(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(InternalTransferStates.AddItems)
    await callback.message.edit_text("🔍 Введите часть названия товара:")


@router.callback_query(F.data == "t_done")
async def finalize_transfer(callback: types.CallbackQuery, state: FSMContext):
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
        "storeFromId": data.get("store_from_id"),
        "storeToId": data.get("store_to_id"),
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
    url = f"{get_base_url()}/resto/api/v2/documents/internal_transfer"
    params = {"key": token}
    
    asyncio.create_task(_send_transfer(bot, chat_id, msg_id, url, params, document))
    await state.clear()


async def _send_transfer(bot: Bot, chat_id: int, msg_id: int, url: str, params: dict, document: dict):
    """Background task to send transfer to iiko"""
    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(url, params=params, json=document, timeout=30.0)
            response.raise_for_status()
        
        await bot.send_message(chat_id, "✅ Перемещение успешно отправлено!")
    except Exception as e:
        logging.error(f"Transfer send error: {e}")
        await bot.send_message(chat_id, f"❌ Ошибка отправки: {e}")
