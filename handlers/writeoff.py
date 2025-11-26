
## ────────────── Импорт библиотек и общих функций ──────────────

import logging
import asyncio
from typing import Optional
import secrets
from aiogram import Bot, Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

try:
    from aiogram.exceptions import TelegramBadRequest
except ImportError:  # fallback for older aiogram builds
    from aiogram.utils.exceptions import TelegramBadRequest  # type: ignore[import]
from sqlalchemy import select
from db.employees_db import async_session
from handlers.base_document import BaseDocumentHandler, _normalize_unit
from handlers.common import (
    get_unit_name_by_id,
    _get_store_id,
    preload_stores,
    list_writeoff_templates,
    get_writeoff_template,
)
from iiko.iiko_auth import get_auth_token, get_base_url
import httpx
from datetime import datetime
from config import DOC_CONFIG
from db.sprav_db import ReferenceData as Accounts
from services.db_queries import DBQueries


## ────────────── Логгер и роутер для aiogram ──────────────
router = Router()
logger = logging.getLogger(__name__)

STORE_PAYMENT_FILTERS = DOC_CONFIG["writeoff"]["stores"]
WRITEOFF_TEMPLATE_SELECTIONS: dict[int, dict[str, str]] = {}


## ────────────── Состояния FSM для акта списания ──────────────
class WriteoffStates(StatesGroup):
    Mode = State()
    TemplateSelect = State()
    Store = State()
    PaymentType = State()
    Reason = State()
    AddItems = State()
    Quantity = State()


## ────────────── Класс обработчика акта списания ──────────────
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
        raw_types = STORE_PAYMENT_FILTERS.get(store_name, [])
        if not raw_types:
            logger.warning("WRITEOFF store %s has no payment types configured", store_name)
            return InlineKeyboardMarkup(inline_keyboard=[])

        type_names = list(dict.fromkeys(raw_types))  # preserve order, drop duplicates

        async with async_session() as session:
            accounts = await DBQueries.get_accounts_by_names(type_names)

        by_name = {}
        for acc in accounts:
            by_name.setdefault(acc.name, acc)

        buttons = []
        for name in type_names:
            acc = by_name.get(name)
            if not acc:
                logger.warning("WRITEOFF account %s missing in DB", name)
                continue
            buttons.append([InlineKeyboardButton(text=acc.name, callback_data=f"w_type:{acc.id}")])

        logger.info(
            "WRITEOFF types for store %s: %s (final buttons: %d)",
            store_name,
            type_names,
            len(buttons)
        )

        return InlineKeyboardMarkup(inline_keyboard=buttons)

    async def format_header(self, data: dict) -> str:
        store = data.get("store_name", "—")
        account = data.get("account_name", "—")
        reason = data.get("reason") or "—"
        author = data.get("user_fullname", "—")

        return (
            f"📄 <b>Акт списания</b>\n"
            f"🏬 <b>Склад:</b> {store}\n"
            f"📂 <b>Тип списания:</b> {account}\n"
            f"📝 <b>Причина:</b> {reason}\n"
            f"👤 <b>Сотрудник:</b> {author}"
        )



## ────────────── Экземпляр обработчика ──────────────
writeoff_handler = WriteoffHandler()


async def _refresh_header(state: FSMContext, bot: Bot, chat_id: int) -> None:
    """Re-render summary message with the current FSM data."""
    data = await state.get_data()
    header_id = data.get("header_msg_id")
    if not header_id:
        logger.warning("WRITEOFF header message is not set yet")
        return
    await writeoff_handler.update_header(bot, chat_id, header_id, data)


async def _set_prompt_message(
    state: FSMContext,
    bot: Bot,
    chat_id: int,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
):
    """Ensure there is a single prompt message under the summary."""
    data = await state.get_data()
    prompt_id = data.get("prompt_msg_id")
    if prompt_id:
        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=prompt_id,
                text=text,
                reply_markup=reply_markup,
            )
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                logger.debug("WRITEOFF prompt unchanged; keeping existing message")
                return
            logger.warning("WRITEOFF failed to edit prompt message: %s", exc)
            prompt_id = None
        except Exception as exc:
            logger.warning("WRITEOFF failed to edit prompt message: %s", exc)
            prompt_id = None

    msg = await bot.send_message(chat_id, text, reply_markup=reply_markup)
    await state.update_data(prompt_msg_id=msg.message_id)


async def _prompt_next_template_item(chat_id: int, bot: Bot, state: FSMContext) -> None:
    data = await state.get_data()
    queue = data.get("template_queue", []) or []
    cursor = data.get("template_cursor", 0)

    if cursor >= len(queue):
        await state.update_data(template_mode=False, template_queue=[], template_cursor=0)
        await state.set_state(WriteoffStates.AddItems)
        prompt_kb = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Отправить", callback_data="w_done")]]
        )
        await _set_prompt_message(
            state,
            bot,
            chat_id,
            "🔍 Введите часть названия товара или нажмите 'Отправить'.",
            reply_markup=prompt_kb,
        )
        return

    item = queue[cursor]
    unit = await get_unit_name_by_id(item.get("mainunit"))
    norm = _normalize_unit(unit)
    if norm == "kg":
        text = f"📏 Сколько грамм для «{item['name']}»?"
    elif norm in ("l", "ml"):
        text = f"📏 Сколько мл для «{item['name']}»?"
    else:
        text = f"📏 Сколько {unit} для «{item['name']}»?"

    msg = await bot.send_message(chat_id, text)
    await state.update_data(
        current_item=item,
        quantity_prompt_id=msg.message_id,
        template_cursor=cursor + 1,
        selection_msg_id=None,
    )
    await state.set_state(WriteoffStates.Quantity)



## ────────────── Основные обработчики FSM акта списания ──────────────


@router.callback_query(F.data == "doc:writeoff")
async def start_writeoff(callback: types.CallbackQuery, state: FSMContext):
    """
    Старт процесса акта списания: выбор режима
    """
    await preload_stores()
    await state.clear()
    logger.info("WRITEOFF start requested by user_id=%s chat_id=%s", callback.from_user.id, callback.message.chat.id)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    mode_keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✍️ Вручную", callback_data="w_mode:manual")],
        [InlineKeyboardButton(text="📂 По шаблону", callback_data="w_mode:template")],
    ])
    await state.set_state(WriteoffStates.Mode)
    await callback.message.edit_text("Как создать акт списания?", reply_markup=mode_keyboard)


async def _start_manual_flow(callback: types.CallbackQuery, state: FSMContext):
    keyboard = await writeoff_handler.get_store_keyboard({})
    await state.set_state(WriteoffStates.Store)
    await state.update_data(prompt_msg_id=callback.message.message_id)
    await callback.message.edit_text("🏬 С какого склада списываем?", reply_markup=keyboard)


async def _prompt_template_choice(callback: types.CallbackQuery, state: FSMContext) -> bool:
    templates = await list_writeoff_templates()
    if not templates:
        return False

    token_map = {secrets.token_hex(3): name for name in templates}
    WRITEOFF_TEMPLATE_SELECTIONS[callback.from_user.id] = token_map
    buttons = [
        [InlineKeyboardButton(text=name, callback_data=f"w_template_pick:{token}")]
        for token, name in token_map.items()
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="w_mode:manual")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await state.set_state(WriteoffStates.TemplateSelect)
    await callback.message.edit_text("📂 Выберите шаблон списания:", reply_markup=keyboard)
    return True


@router.callback_query(F.data.startswith("w_mode:"))
async def choose_writeoff_mode(callback: types.CallbackQuery, state: FSMContext):
    mode = callback.data.split(":", 1)[1]
    if mode == "manual":
        await _start_manual_flow(callback, state)
        await callback.answer()
    elif mode == "template":
        has_templates = await _prompt_template_choice(callback, state)
        if has_templates:
            await callback.answer()
        else:
            await callback.answer("Нет шаблонов списаний", show_alert=True)
    else:
        await callback.answer("Неизвестный режим", show_alert=True)


@router.callback_query(F.data.startswith("w_template_pick:"))
async def choose_writeoff_template(callback: types.CallbackQuery, state: FSMContext):
    token = callback.data.split(":", 1)[1]
    user_map = WRITEOFF_TEMPLATE_SELECTIONS.get(callback.from_user.id, {})
    template_name = user_map.get(token)
    if not template_name:
        await callback.answer("Шаблон не найден", show_alert=True)
        return
    user_map.pop(token, None)

    template = await get_writeoff_template(template_name)
    if not template:
        await callback.answer("Шаблон удалён", show_alert=True)
        return

    started = await _start_template_flow(callback, state, template)
    if started:
        await callback.answer()
    else:
        await callback.answer("В шаблоне нет позиций", show_alert=True)


async def _start_template_flow(callback: types.CallbackQuery, state: FSMContext, template) -> bool:
    queue = list(template.items or [])
    if not queue:
        return False

    reason = (template.reason or "").strip()
    needs_reason = not reason
    tg_id = str(callback.from_user.id)
    full_name = await writeoff_handler.get_employee_name(tg_id)
    await state.update_data(
        store_name=template.store_name,
        store_id=template.store_id,
        account_name=template.account_name,
        account_id=template.account_id,
        reason=reason if reason else None,
        user_fullname=full_name,
        items=[],
        prompt_msg_id=None,
        template_mode=True,
        template_queue=queue,
        template_cursor=0,
    )

    await callback.message.edit_text("📄 Акт списания\n(заполняется...)")
    await state.update_data(header_msg_id=callback.message.message_id)
    await _refresh_header(state, callback.message.bot, callback.message.chat.id)
    if needs_reason:
        await state.set_state(WriteoffStates.Reason)
        await _set_prompt_message(
            state,
            callback.message.bot,
            callback.message.chat.id,
            "📝 Введите причину списания для этого шаблона:",
        )
    else:
        await _prompt_next_template_item(callback.message.chat.id, callback.message.bot, state)
    return True


@router.callback_query(F.data.startswith("w_store:"))
async def choose_store(callback: types.CallbackQuery, state: FSMContext):
    """
    Обработка выбора склада
    """
    store_name = callback.data.split(":")[1]
    store_id = await _get_store_id(store_name)
    if not store_id:
        return await callback.answer("❌ Ошибка определения склада")
    
    await state.update_data(store_name=store_name, store_id=store_id)
    logger.info("WRITEOFF store selected: %s (%s) by user %s", store_name, store_id, callback.from_user.id)
    tg_id = str(callback.from_user.id)
    full_name = await writeoff_handler.get_employee_name(tg_id)
    await state.update_data(user_fullname=full_name, prompt_msg_id=None)

    # Переводим предыдущее сообщение (с опросом) в summary
    await callback.message.edit_text("📄 Акт списания\n(заполняется...)")
    await state.update_data(header_msg_id=callback.message.message_id)
    await _refresh_header(state, callback.message.bot, callback.message.chat.id)

    data = await state.get_data()
    await state.set_state(WriteoffStates.PaymentType)
    keyboard = await writeoff_handler.get_doc_type_keyboard(data)
    await _set_prompt_message(
        state,
        callback.message.bot,
        callback.message.chat.id,
        "📂 Какой тип списания?",
        reply_markup=keyboard,
    )


@router.callback_query(F.data.startswith("w_type:"))
async def choose_type(callback: types.CallbackQuery, state: FSMContext):
    """
    Обработка выбора типа списания
    """
    type_id = callback.data.split(":")[1]
    async with async_session() as session:
        result = await session.execute(select(Accounts).where(Accounts.id == type_id))
        account = result.scalar_one()
    
    await state.update_data(account_name=account.name, account_id=type_id)
    logger.info("WRITEOFF type selected: %s (%s)", account.name, type_id)
    await _refresh_header(state, callback.message.bot, callback.message.chat.id)
    await state.set_state(WriteoffStates.Reason)
    await _set_prompt_message(
        state,
        callback.message.bot,
        callback.message.chat.id,
        "📝 Введите причину списания:",
    )


@router.message(WriteoffStates.Reason)
async def set_reason(message: types.Message, state: FSMContext):
    """Сохраняет причину списания и переходит к поиску товаров."""
    reason = (message.text or "").strip()
    await message.delete()
    if not reason:
        await message.answer("❌ Причина не может быть пустой")
        return

    logger.info("WRITEOFF reason set: %s", reason)
    data = await state.get_data()
    await state.update_data(reason=reason)

    await _refresh_header(state, message.bot, message.chat.id)

    if data.get("template_mode"):
        await _prompt_next_template_item(message.chat.id, message.bot, state)
        return

    await state.set_state(WriteoffStates.AddItems)
    await _set_prompt_message(
        state,
        message.bot,
        message.chat.id,
        "🔍 Введите часть названия товара:",
    )


@router.message(WriteoffStates.AddItems)
async def search_products(message: types.Message, state: FSMContext):
    """
    Поиск и выбор товара для списания
    """
    query = message.text.strip()
    await message.delete()
    
    results = await DBQueries.search_nomenclature(
        query,
        types=["GOODS", "PREPARED"],
        parents=None,
        use_parent_filters=False,
    )
    logger.info("WRITEOFF search query '%s' returned %d results", query, len(results))
    
    if not results:
        return await message.answer("🔎 Ничего не найдено.")
    
    data = await state.get_data()
    await state.update_data(nomenclature_cache={r['id']: r for r in results})
    
    kb = writeoff_handler.build_item_keyboard(results, "w_item")
    selection_msg_id = data.get("selection_msg_id")
    if selection_msg_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=selection_msg_id,
                text="Выберите товар:",
                reply_markup=kb,
            )
        except Exception as exc:
            logger.warning("WRITEOFF unable to reuse selection message: %s", exc)
            msg = await message.answer("Выберите товар:", reply_markup=kb)
            selection_msg_id = msg.message_id
    else:
        msg = await message.answer("Выберите товар:", reply_markup=kb)
        selection_msg_id = msg.message_id

    await state.update_data(selection_msg_id=selection_msg_id)


@router.callback_query(F.data.startswith("w_item:"))
async def select_item(callback: types.CallbackQuery, state: FSMContext):
    """
    Подтверждение выбора товара и ввод количества
    """
    item_id = callback.data.split(":")[1]
    data = await state.get_data()
    cache = data.get("nomenclature_cache", {})
    item = cache.get(item_id)
    
    if not item:
        return await callback.answer("❌ Товар не найден")
    logger.info("WRITEOFF item selected: %s (%s)", item.get("name"), item_id)
    
    unit = await get_unit_name_by_id(item["mainunit"])
    norm = _normalize_unit(unit)
    
    if norm == "kg":
        text = f"📏 Сколько грамм для «{item['name']}»?"
    elif norm in ("l", "ml"):
        text = f"📏 Сколько мл для «{item['name']}»?"
    else:
        text = f"📏 Сколько {unit} для «{item['name']}»?"
    
    await state.update_data(current_item=item, selection_msg_id=None, quantity_prompt_id=callback.message.message_id)
    await state.set_state(WriteoffStates.Quantity)
    await callback.message.edit_text(text)


@router.message(WriteoffStates.Quantity)
async def save_quantity(message: types.Message, state: FSMContext):
    """
    Сохраняет количество для выбранного товара
    """
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
    
    prompt_id = data.get("quantity_prompt_id")
    if prompt_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=prompt_id)
        except Exception:
            logger.warning("WRITEOFF unable to remove quantity prompt")

    await state.update_data(items=items, current_item=None, quantity_prompt_id=None)
    logger.info(
        "WRITEOFF quantity saved: item=%s qty=%s normalized=%s total_items=%d",
        item.get("name"),
        item.get("user_quantity"),
        item.get("quantity"),
        len(items)
    )
    await message.delete()

    await _refresh_header(state, message.bot, message.chat.id)

    if data.get("template_mode"):
        await _prompt_next_template_item(message.chat.id, message.bot, state)
        return

    await state.set_state(WriteoffStates.AddItems)
    prompt_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Отправить", callback_data="w_done")]]
    )
    await _set_prompt_message(
        state,
        message.bot,
        message.chat.id,
        "🔍 Введите часть названия товара или нажмите 'Отправить'.",
        reply_markup=prompt_kb,
    )


@router.callback_query(F.data == "w_done")
async def finalize_writeoff(callback: types.CallbackQuery, state: FSMContext):
    """
    Завершение и отправка акта списания в iiko
    """
    data = await state.get_data()
    items = data.get("items", [])
    
    if not items:
        return await callback.answer("❌ Добавьте хотя бы один товар")
    
    await callback.message.edit_text("⏳ Отправляем в iiko...")
    
    date_now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    document = {
        "dateIncoming": date_now,
        "status": "PROCESSED",
        "comment": data.get("reason", ""),
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
    
    logger.info(
        "WRITEOFF sending document: store=%s account=%s items=%d",
        document.get("storeId"),
        document.get("accountId"),
        len(document.get("items", []))
    )
    asyncio.create_task(_send_writeoff(bot, chat_id, msg_id, url, params, document))
    await state.clear()


async def _send_writeoff(bot: Bot, chat_id: int, msg_id: int, url: str, params: dict, document: dict):
    """
    Фоновая задача отправки акта списания в iiko
    """
    try:
        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(url, params=params, json=document, timeout=30.0)
            response.raise_for_status()
        
        logger.info("WRITEOFF document sent successfully: doc=%s", document)
        await bot.send_message(chat_id, "✅ Акт списания успешно отправлен!")
    except Exception as e:
        logger.exception("WRITEOFF send error")
        await bot.send_message(chat_id, f"❌ Ошибка отправки: {e}")
