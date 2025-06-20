import httpx
from html import escape
from aiogram import Router, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from db.employees_db import async_session
from handlers.template_creation import PreparationTemplate  # Убедись, что модель импортирована правильно
from aiogram.fsm.context import FSMContext
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from aiogram.fsm.state import State, StatesGroup
from sqlalchemy.orm import declarative_base
from sqlalchemy import Column, String
from iiko.iiko_auth import get_auth_token, get_base_url


router = Router()

PIZZAYOLO_CONCEPTION_ID = "cd6b8810-0f57-4e1e-82a4-3f60fb2ded7a"
Base = declarative_base()

class ReferenceData(Base):
    __tablename__ = "reference_data"

    id = Column(String, primary_key=True)
    root_type = Column(String)
    name = Column(String)
    code = Column(String)


class TemplateFill(StatesGroup):
    Filling = State()
    AwaitQuantity = State()
# Получение всех шаблонов из базы




async def format_template_info(template) -> str:
    from_store = await get_name_by_id("Store", template.from_store_id) if template.from_store_id else "—"
    to_store = await get_name_by_id("Store", template.to_store_id) if template.to_store_id else "—"
    supplier = await get_name_by_id("Supplier", template.supplier_id) if template.supplier_id else "—"

    return (
        f"📦 <b>Шаблон: {template.name}</b>\n"
        f"🔁 Склад откуда: {from_store}\n"
        f"➡️ Склад куда: {to_store}\n"
        f"🚚 Поставщик: {supplier}"
    )

async def get_name_by_id(type_: str, id_: str) -> str:
    async with async_session() as session:
        result = await session.execute(
            select(ReferenceData.name)
            .where(ReferenceData.id == id_)
            .where(ReferenceData.root_type == type_)
        )
        return result.scalar_one_or_none() or "—"

async def get_template_keyboard():
    async with async_session() as session:
        result = await session.execute(select(PreparationTemplate.name))
        templates = result.scalars().all()

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=name, callback_data=f"template:select:{name}")]
                for name in templates
            ]
        )
        return keyboard

async def get_all_templates():
    async with async_session() as session:
        result = await session.execute(select(PreparationTemplate.name))
        templates = result.scalars().all()
        return templates

async def get_template_by_name(template_name: str):
    async with async_session() as session:
        result = await session.execute(
            select(PreparationTemplate)
            .where(PreparationTemplate.name == template_name)
        )
        return result.scalar_one_or_none()



async def get_unit_name_by_id(unit_id: str) -> str:
    async with async_session() as session:
        result = await session.execute(
            select(ReferenceData.name)
            .where(ReferenceData.id == unit_id)
            .where(ReferenceData.root_type == "MeasureUnit")
        )
        return result.scalar_one_or_none() or "шт"


async def ask_quantity(message: types.Message, item: dict, state: FSMContext):
    data = await state.get_data()
    prev_msg_id = data.get("prev_msg_id")
    unit_name = await get_unit_name_by_id(item["mainunit"])
    text = f"📏 Сколько {unit_name} для «{item['name']}»?"

    if prev_msg_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=prev_msg_id,
                text=text
            )
            return
        except Exception:
            pass  # если не удалось редактировать — создаём новое

    msg = await message.answer(text)
    await state.update_data(prev_msg_id=msg.message_id)

@router.callback_query(F.data.startswith("template:select:"))
async def handle_template_selected(callback: types.CallbackQuery, state: FSMContext):
    template_name = callback.data.split(":")[-1]
    await callback.message.edit_text(f"🔄 Обработка шаблона: {template_name}")
    # здесь вызывай функцию, которая начнёт шаги по заполнению документа


# Вывод всех шаблонов кнопками
@router.callback_query(F.data == "prep:by_template")
async def handle_prep_choice(callback: types.CallbackQuery):
    templates = await get_all_templates()

    if not templates:
        await callback.message.edit_text("⚠️ Нет доступных шаблонов.")
        return

    buttons = [
        [InlineKeyboardButton(text=tpl, callback_data=f"use_template:{tpl}")]
        for tpl in templates
    ]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await callback.message.edit_text("📋 Выберите шаблон:", reply_markup=keyboard)

@router.callback_query(F.data.startswith("use_template:"))
async def use_template_handler(callback: types.CallbackQuery, state: FSMContext):
    template_name = callback.data.split(":", 1)[1]
    template = await get_template_by_name(template_name)

    if not template:
        await callback.message.edit_text("⚠️ Шаблон не найден.")
        return

    await state.update_data(
        template_items=template.items,
        current_index=0,
        prev_msg_id=callback.message.message_id,
        from_store_id=template.from_store_id,
        to_store_id=template.to_store_id,
        supplier_id=template.supplier_id,
        supplier_name=template.supplier_name,
        template_name=template.name
    )

    # первый вопрос
    header = await format_template_info(template)
    first_question = f"🔢 Сколько {await get_unit_name_by_id(template.items[0]['mainunit'])} для «{template.items[0]['name']}»?"
    # Отправляем информацию о шаблоне
    header_msg = await callback.message.edit_text(header, parse_mode="HTML")

    # Отправляем первый вопрос отдельно
    first_question = f"🔢 Сколько {await get_unit_name_by_id(template.items[0]['mainunit'])} для «{template.items[0]['name']}»?"
    question_msg = await callback.message.answer(first_question)

    # Обновляем состояние
    await state.update_data(
        template_items=template.items,
        current_index=0,
        from_store_id=template.from_store_id,
        to_store_id=template.to_store_id,
        supplier_id=template.supplier_id,
        supplier_name=template.supplier_name,
        template_name=template.name,
        prev_msg_id=question_msg.message_id  # только сообщение с вопросом
    )

    await state.set_state(TemplateFill.AwaitQuantity)

async def send_invoice_to_iiko(xml_data: str) -> str:
    try:
        token = await get_auth_token()
    except Exception as e:
        return f"❌ Авторизация в iiko не удалась: {e}"

    url = f"{get_base_url()}/resto/api/documents/import/outgoingInvoice"
    headers = {"Content-Type": "application/xml"}
    params = {"key": token}

    async with httpx.AsyncClient(verify=False) as client:
        try:
            response = await client.post(url, params=params, content=xml_data, headers=headers)
            response.raise_for_status()
            return f"✅ Расходная накладная отправлена в iiko.\nОтвет:\n{response.text}"
        except httpx.HTTPError as e:
            return f"❌ Ошибка отправки расходной накладной: {e.response.status_code}\n{e.response.text}"

def build_invoice_xml(template: dict) -> str:
    from datetime import datetime
    date_now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    doc_number = "tg-inv-" + datetime.now().strftime("%Y%m%d%H%M%S")

    items_xml = ""
    for item in template["items"]:
        price = float(item["price"])
        amount = float(item["quantity"])
        sum_total = round(price * amount, 2)

        items_xml += f"""
        <item>
            <productId>{item['id']}</productId>
            <storeId>{template['to_store_id']}</storeId>
            <price>{price:.2f}</price>
            <amount>{amount:.2f}</amount>
            <sum>{sum_total:.2f}</sum>
        </item>"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<document>
    <documentNumber>{doc_number}</documentNumber>
    <dateIncoming>{date_now}</dateIncoming>
    <useDefaultDocumentTime>true</useDefaultDocumentTime>
    <revenueAccountCode>4.08</revenueAccountCode>
    <counteragentId>{template['supplier_id']}</counteragentId>
    <defaultStoreId>{template['to_store_id']}</defaultStoreId>
    <conceptionId>{PIZZAYOLO_CONCEPTION_ID}</conceptionId>
    <comment>Создано автоматически на основе акта приготовления</comment>
    <items>{items_xml}
    </items>
</document>"""
    return xml



def build_xml(template: dict) -> str:
    from datetime import datetime
    date_now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    doc_number = "tg-" + datetime.now().strftime("%Y%m%d%H%M%S")

    items_xml = ""
    for idx, item in enumerate(template["items"], start=1):
        items_xml += f"""
        <item>
            <num>{idx}</num>
            <product>{item['id']}</product>
            <amount>{item['quantity']}</amount>
            <amountUnit>{item['mainunit']}</amountUnit>
        </item>"""

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<document>
    <storeFrom>{template['from_store_id']}</storeFrom>
    <storeTo>{template['to_store_id']}</storeTo>
    <dateIncoming>{date_now}</dateIncoming>
    <documentNumber>{doc_number}</documentNumber>
    <conception>{PIZZAYOLO_CONCEPTION_ID}</conception>
    <comment>Создано через Telegram-бота</comment>
    <items>{items_xml}
    </items>
</document>"""
    return xml


async def send_to_iiko(xml_data: str) -> str:
    try:
        token = await get_auth_token()
    except Exception as e:
        return f"❌ Не удалось авторизоваться в iiko: {e}"

    url = f"{get_base_url()}/resto/api/documents/import/productionDocument"
    headers = {
        "Content-Type": "application/xml"
    }
    params = {
        "key": token
    }

    async with httpx.AsyncClient(verify=False) as client:
        try:
            response = await client.post(url, params=params, content=xml_data, headers=headers)
            response.raise_for_status()
            return f"📤 Успешно отправлено в iiko.\nОтвет:\n{response.text}"
        except httpx.HTTPError as e:
            return f"❌ Ошибка отправки в iiko: {e.response.status_code}\n{e.response.text}"
        except Exception as e:
            return f"❌ Неизвестная ошибка при отправке: {e}"



@router.message(TemplateFill.AwaitQuantity)
async def handle_quantity_input(message: types.Message, state: FSMContext):
    user_data = await state.get_data()
    current_index = user_data.get("current_index", 0)
    items = user_data["template_items"]

    try:
        qty = float(message.text.replace(",", "."))
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
        except Exception as e:
            print(f"❌ Не удалось удалить сообщение пользователя: {e}")
    except ValueError:
        await message.answer("❌ Введите корректное число.")
        return

    items[current_index]["quantity"] = qty

    if current_index + 1 < len(items):
        next_index = current_index + 1
        await state.update_data(
            template_items=items,
            current_index=next_index
        )
        await ask_quantity(message, items[next_index], state)
    else:
        prev_msg_id = user_data.get("prev_msg_id")
        msg_text = "✅ Все позиции заполнены. Шаблон готов к применению."

        if prev_msg_id:
            try:
                await message.bot.edit_message_text(
                    chat_id=message.chat.id,
                    message_id=prev_msg_id,
                    text=msg_text
                )
            except Exception:
                await message.answer(msg_text)
        else:
            await message.answer(msg_text)

        final_data = {
            "name": user_data.get("template_name"),
            "from_store_id": user_data.get("from_store_id"),
            "to_store_id": user_data.get("to_store_id"),
            "supplier_id": user_data.get("supplier_id"),
            "supplier_name": user_data.get("supplier_name"),
            "items": items
        }

        print("✅ ИТОГОВЫЙ ШАБЛОН:\n", final_data)

        # Генерация XML и отправка
        xml_data = build_xml(final_data)
        print("📦 Отправляем XML в iiko:\n", xml_data)
        result_msg = await send_to_iiko(xml_data)

        invoice_msg = ""
        if final_data.get("supplier_id") and all("price" in i for i in items):
            invoice_xml = build_invoice_xml(final_data)
            invoice_msg = await send_invoice_to_iiko(invoice_xml)
        else:
            invoice_msg = "ℹ️ Расходная накладная не создавалась."

        escaped = escape(result_msg + "\n\n" + invoice_msg)
        await message.answer(f"<pre>{escaped}</pre>", parse_mode="HTML")

        await state.clear()





