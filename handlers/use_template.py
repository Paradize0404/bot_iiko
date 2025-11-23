## ────────────── Импорт библиотек и общих функций ──────────────
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
    build_invoice_xml,
    post_xml,
)

router = Router()
logger = logging.getLogger(__name__)


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
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=f"use_template:{t}")] for t in templates])
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

    # Получаем имя склада из БД
    from db.stores_db import Store, async_session as stores_session
    from sqlalchemy import select
    async with stores_session() as s:
        store_result = await s.execute(select(Store.name).where(Store.id == tpl.from_store_id))
        store_name = store_result.scalar_one_or_none() or "—"

    # Предзагружаем имена единиц измерения для всех позиций (оптимизация)
    unit_names = {}
    for item in tpl.items:
        unit_id = item.get('mainunit')
        if unit_id and unit_id not in unit_names:
            unit_names[unit_id] = await get_unit_name(unit_id)

    # Формируем список позиций с ценами
    items_lines = []
    for item in tpl.items:
        price = item.get('price', '—')
        items_lines.append(f"  • {item['name']}: — × {price} ₽")
    
    header = (
        f"📦 <b>Шаблон: {tpl.name}</b>\n"
        f"🏪 Склад: {store_name}\n"
        f"🚚 Поставщик: {tpl.supplier_name or '—'}\n\n"
        f"🍕 <b>Позиции:</b>\n" +
        "\n".join(items_lines)
    )
    status_msg = await callback.message.edit_text(header, parse_mode="HTML")

    first = tpl.items[0]
    first_unit = unit_names.get(first['mainunit'], 'шт')
    q_text = f"🔢 Сколько {first_unit} для «{first['name']}»?"
    q_msg = await callback.message.answer(q_text)

    await state.update_data(
        template_items=tpl.items,
        current_index=0,
        prev_msg_id=q_msg.message_id,
        status_message_id=status_msg.message_id,
        from_store_id=tpl.from_store_id,
        to_store_id=tpl.to_store_id,
        supplier_id=tpl.supplier_id,
        supplier_name=tpl.supplier_name,
        template_name=tpl.name,
        store_name=store_name,
        unit_names=unit_names,  # Сохраняем кэш единиц измерения
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
    unit_names = data.get('unit_names', {})  # Используем кэш
    
    try:
        qty = float(message.text.replace(',', '.'))
    except ValueError:
        return await message.answer("❌ Введите корректное число.")

    items[idx]['quantity'] = qty
    await state.update_data(template_items=items)
    
    # Обновляем статусное сообщение с количествами, ценами и итоговой суммой (используем кэш единиц)
    items_lines = []
    total_sum = 0
    for i, item in enumerate(items):
        unit = unit_names.get(item['mainunit'], 'шт')  # Берем из кэша вместо БД
        price = item.get('price', 0)
        if item.get('quantity') is not None:
            item_sum = float(item['quantity']) * float(price)
            total_sum += item_sum
            items_lines.append(f"  • {item['name']}: {item['quantity']} {unit} × {price} ₽ = {item_sum:.2f} ₽")
        else:
            items_lines.append(f"  • {item['name']}: — × {price} ₽")
    
    header = (
        f"📦 <b>Шаблон: {data.get('template_name')}</b>\n"
        f"🏪 Склад: {data.get('store_name')}\n"
        f"🚚 Поставщик: {data.get('supplier_name') or '—'}\n\n"
        f"🍕 <b>Позиции:</b>\n" +
        "\n".join(items_lines) +
        f"\n\n💰 <b>Итого: {total_sum:.2f} ₽</b>"
    )
    
    try:
        await message.bot.edit_message_text(
            chat_id=message.chat.id,
            message_id=data.get('status_message_id'),
            text=header,
            parse_mode="HTML"
        )
    except Exception:
        pass  # Игнорируем ошибки "message is not modified"

    if idx + 1 < len(items):
        await state.update_data(current_index=idx+1)
        await message.delete()
        unit = unit_names.get(items[idx+1]['mainunit'], 'шт')  # Берем из кэша
        await message.bot.edit_message_text(chat_id=message.chat.id, message_id=data.get('prev_msg_id'), text=f"🔢 Сколько {unit} для «{items[idx+1]['name']}»?")
        return

    # finished - все количества собраны, показываем кнопки Отправить/Отмена
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

    # Сохраняем final в state для последующей отправки
    await state.update_data(final_data=final)

    # Показываем сводку с ценами и итоговой суммой (используем кэш)
    summary_lines = [f"📦 <b>{final['name']}</b>"]
    total_sum = 0
    for it in items:
        unit = unit_names.get(it['mainunit'], 'шт')  # Берем из кэша
        item_sum = float(it['quantity']) * float(it['price'])
        total_sum += item_sum
        summary_lines.append(f"  • {it['name']}: {it['quantity']} {unit} × {it['price']} ₽ = {item_sum:.2f} ₽")
    
    summary_lines.append(f"\n💰 <b>Итого: {total_sum:.2f} ₽</b>")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить", callback_data="confirm_send_invoice")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_send_invoice")]
    ])
    
    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=data.get('prev_msg_id'),
        text="\n".join(summary_lines),
        parse_mode="HTML",
        reply_markup=kb
    )


## ────────────── Обработка нажатия "Отмена" ──────────────
@router.callback_query(F.data == "cancel_send_invoice")
async def cancel_invoice(callback: types.CallbackQuery, state: FSMContext):
    """
    Отмена отправки накладной и сброс FSM
    """
    await callback.message.edit_text("❌ Отменено.")
    await state.clear()
    await callback.answer()


## ────────────── Обработка нажатия "Отправить" ──────────────
@router.callback_query(F.data == "confirm_send_invoice")
async def confirm_and_send_invoice(callback: types.CallbackQuery, state: FSMContext):
    """
    Фоновая отправка расходной накладной в iiko
    Пропускает позиции с нулевым количеством
    """
    data = await state.get_data()
    final = data.get('final_data')
    if not final:
        await callback.message.edit_text("⚠️ Данные не найдены.")
        await state.clear()
        return

    # Показываем процесс отправки
    await callback.message.edit_text("⏳ Отправляется...")

    # Фильтруем позиции с нулевым количеством
    filtered_items = [it for it in final['items'] if it.get('quantity', 0) > 0]
    
    if not filtered_items:
        await callback.message.edit_text("⚠️ Все позиции имеют нулевое количество, накладная не отправлена.")
        await state.clear()
        return

    final['items'] = filtered_items

    # Отправляем накладную
    inv_xml = build_invoice_xml(final)
    ok, resp = await post_xml('/resto/api/documents/import/outgoingInvoice', inv_xml)

    if ok:
        await callback.message.edit_text("✅ Расходная накладная успешно отправлена в iiko!")
    else:
        await callback.message.edit_text(f"❌ Ошибка при отправке накладной:\n<pre>{escape(resp)}</pre>", parse_mode='HTML')

    await state.clear()
    await callback.answer()
