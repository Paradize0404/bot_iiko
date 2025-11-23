import httpx
import logging
import pprint
from html import escape
from aiogram import Router, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from db.employees_db import async_session
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String
from iiko.iiko_auth import get_auth_token, get_base_url

router = Router()

logger = logging.getLogger(__name__)

PIZZAYOLO_CONCEPTION_ID = "cd6b8810-0f57-4e1e-82a4-3f60fb2ded7a"

import os
import httpx
import logging
import pprint
from html import escape
from aiogram import Router, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from handlers.common import (
    PreparationTemplate,
    get_name,
    get_unit_name,
    list_templates,
    get_template,
    build_production_xml,
    build_invoice_xml,
    post_xml,
)
import logging, pprint

router = Router()
logger = logging.getLogger(__name__)


## ────────────── Импорт библиотек и общих функций ──────────────

## ────────────── Состояния FSM для применения шаблона ──────────────
class TemplateFill(StatesGroup):
    """
    Состояния FSM для применения шаблона:
    - AwaitQuantity: ввод количества для каждой позиции
    """
    AwaitQuantity = State()


## ────────────── Старт применения шаблона ──────────────
@router.callback_query(F.data == "prep:by_template")
async def show_templates(c: types.CallbackQuery):
    """
    Показывает список доступных шаблонов для выбора
    """
    templates = await list_templates()
    if not templates:
        return await c.message.edit_text("⚠️ Нет доступных шаблонов.")
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(t, callback_data=f"use_template:{t}")] for t in templates])
    await c.message.edit_text("📋 Выберите шаблон:", reply_markup=kb)


## ────────────── Выбор шаблона для применения ──────────────
@router.callback_query(F.data.startswith("use_template:"))
async def use_template_handler(callback: types.CallbackQuery, state: FSMContext):
    """
    Обработка выбора шаблона и запуск FSM заполнения позиций
    """
    name = callback.data.split(":", 1)[1]
    tpl = await get_template(name)
    if not tpl:
        return await callback.message.edit_text("⚠️ Шаблон не найден.")

    header = (
        f"📦 <b>Шаблон: {tpl.name}</b>\n"
        f"🔁 Склад откуда: {await get_name('Store', tpl.from_store_id)}\n"
# ────────────── Логгер и роутер для aiogram ──────────────
        f"➡️ Склад куда: {await get_name('Store', tpl.to_store_id)}\n"
        f"🚚 Поставщик: {await get_name('Supplier', tpl.supplier_id)}"
    )
    await callback.message.edit_text(header, parse_mode="HTML")

# ────────────── Состояния FSM для применения шаблона ──────────────
    first = tpl.items[0]
    q_text = f"🔢 Сколько {await get_unit_name(first['mainunit'])} для «{first['name']}»?"
    q_msg = await callback.message.answer(q_text)

    await state.update_data(
        template_items=tpl.items,
        current_index=0,
        prev_msg_id=q_msg.message_id,

# ────────────── Выбор шаблона для применения ──────────────
        from_store_id=tpl.from_store_id,
        to_store_id=tpl.to_store_id,
        supplier_id=tpl.supplier_id,
        supplier_name=tpl.supplier_name,
        template_name=tpl.name,
    )
    await state.set_state(TemplateFill.AwaitQuantity)


## ────────────── Ввод количества для каждой позиции ──────────────
@router.message(TemplateFill.AwaitQuantity)
async def handle_quantity_input(message: types.Message, state: FSMContext):
    """
    Обработка ввода количества для каждой позиции шаблона
    """
    data = await state.get_data()
    idx = data.get('current_index', 0)
    items = data.get('template_items', [])
    try:
        qty = float(message.text.replace(',', '.'))
    except ValueError:
        return await message.answer("❌ Введите корректное число.")

    items[idx]['quantity'] = qty
    await state.update_data(template_items=items)

    if idx + 1 < len(items):
        await state.update_data(current_index=idx+1)
        await message.delete()
        unit = await get_unit_name(items[idx+1]['mainunit'])
        await message.bot.edit_message_text(chat_id=message.chat.id, message_id=data.get('prev_msg_id'), text=f"🔢 Сколько {unit} для «{items[idx+1]['name']}»?")
        return

    # finished

# ────────────── Ввод количества для каждой позиции ──────────────
    await message.delete()
    final = {
        'name': data.get('template_name'),
        'from_store_id': data.get('from_store_id'),
        'to_store_id': data.get('to_store_id'),
        'supplier_id': data.get('supplier_id'),
        'supplier_name': data.get('supplier_name'),
        'items': items,
    }
    logger.info('Итог шаблона: %s', pprint.pformat(final, width=120))

    xml = build_production_xml(final)
    ok, resp = await post_xml('/resto/api/documents/import/productionDocument', xml)
    result_msg = ("✅ Отправлено в iiko" if ok else "❌ Ошибка отправки") + f"\n{resp}"

    invoice_msg = "ℹ️ Расходная накладная не создавалась."
    if final.get('supplier_id') and all('price' in it for it in items):
        inv_xml = build_invoice_xml(final)
        ok2, resp2 = await post_xml('/resto/api/documents/import/outgoingInvoice', inv_xml)
        invoice_msg = ("✅ Накладная отправлена" if ok2 else "❌ Ошибка накладной") + f"\n{resp2}"

    await message.answer(f"<pre>{escape(result_msg + '\n\n' + invoice_msg)}</pre>", parse_mode='HTML')
    await state.clear()

