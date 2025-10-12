

import re
import logging
from aiogram import Bot
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import select, Column, String
from sqlalchemy.orm import declarative_base
from db.employees_db import async_session, Employee
from handlers.template_creation import STORE_CACHE, preload_stores, search_nomenclature, Nomenclature
from handlers.use_template import get_unit_name_by_id
from iiko.iiko_auth import get_auth_token, get_base_url
from html import escape
import httpx
from datetime import datetime
import asyncio
from keyboards.main_keyboard import cancel_process
router = Router()

Base = declarative_base()

class Accounts(Base):
    __tablename__ = "accounts"

    id = Column(String, primary_key=True)
    # root_type = Column(String)
    name = Column(String)
    code = Column(String)

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
    "Кухня": ["Списание кухня порча", "Списание кухня проработка", "Питание персонала"]
}


async def search_nomenclature_by_type(partial_name: str, required_type: str = "product") -> list[dict]:
    async with async_session() as session:
        terms = [t.strip() for t in partial_name.lower().split() if t.strip()]
        if not terms:
            return []

        query = select(Nomenclature.id, Nomenclature.name, Nomenclature.mainunit).limit(50)
        query = query.where(Nomenclature.type == required_type)

        for term in terms:
            query = query.where(Nomenclature.name.ilike(f"%{term}%"))

        result = await session.execute(query)
        rows = result.all()
        return [{"id": r.id, "name": r.name, "mainunit": r.mainunit} for r in rows]


async def search_nomenclature_for_writeoff(partial_name: str) -> list[dict]:
    async with async_session() as session:
        terms = [t.strip() for t in partial_name.lower().split() if t.strip()]
        if not terms:
            return []

        query = select(Nomenclature.id, Nomenclature.name, Nomenclature.mainunit, Nomenclature.type).limit(50)
        query = query.where(Nomenclature.type.in_(["GOODS", "PREPARED"]))

        for term in terms:
            query = query.where(Nomenclature.name.ilike(f"%{term}%"))

        result = await session.execute(query)
        rows = result.all()
        return [{"id": r.id, "name": r.name, "mainunit": r.mainunit} for r in rows]


async def update_writeoff_header(bot: Bot, chat_id: int, msg_id: int, data: dict):
    store = data.get("store_name", "—")
    account = data.get("account_name", "—")
    reason = data.get("reason", "—")
    comment = data.get("comment", "—")
    author = data.get("user_fullname", "—")
    items = data.get("items", [])

    text = (
        f"📄 <b>Акт списания</b>\n"
        f"🏬 <b>Склад:</b> {store}\n"
        f"📂 <b>Тип списания:</b> {account}\n"
        f"📝 <b>Причина:</b> {reason}\n"
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



@router.callback_query(F.data == "doc:writeoff")
async def start_writeoff(callback: types.CallbackQuery, state: FSMContext):
    await preload_stores()
    await state.clear()
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"w_store:{name}")]
        for name in STORE_PAYMENT_FILTERS
    ])
    await state.set_state(WriteoffStates.Store)
    if (
        callback.message.text != "🏬 С какого склада списываем?"
        or callback.message.reply_markup != keyboard
    ):
        await callback.message.edit_text("🏬 С какого склада списываем?", reply_markup=keyboard)

@router.callback_query(F.data.startswith("w_store:"))
async def choose_store(callback: types.CallbackQuery, state: FSMContext):
    store_name = callback.data.split(":")[1]
    store_id = STORE_CACHE.get(f"{store_name} Пиццерия")
    if not store_id:
        return await callback.answer("❌ Ошибка определения склада")
    await state.update_data(store_name=store_name, store_id=store_id)

    async with async_session() as session:
        names = STORE_PAYMENT_FILTERS[store_name]
        print(f"🔍 Поиск типов списания для склада: {store_name}")
        print(f"➡️ Ожидаемые названия: {names}")

        result = await session.execute(
            select(Accounts)
            # .where(Accounts.root_type == "PaymentType")
            .where(Accounts.name.in_(names))
        )
        filtered = result.scalars().all()

        print(f"✅ Найдено в базе: {[f'{pt.name} ({pt.id})' for pt in filtered]}")

        if not filtered:
            await callback.message.edit_text("⚠️ Не найдено ни одного подходящего типа списания в базе.")
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=pt.name, callback_data=f"w_type:{pt.id}")]
                             for pt in filtered]
        )
        await state.set_state(WriteoffStates.PaymentType)

        # 🧩 Сохраняем ID исходного сообщения — туда запишем заголовок
        await state.update_data(header_msg_id=callback.message.message_id)

        # 🔐 Имя пользователя
        tg_id = str(callback.from_user.id)
        async with async_session() as session:
            result = await session.execute(select(Employee).where(Employee.telegram_id == tg_id))
            user = result.scalar_one_or_none()
            full_name = f"{user.first_name} {user.last_name}" if user else "Неизвестно"

        await state.update_data(user_fullname=full_name)



        # ⬇️ Теперь под шапкой отправляем типы списания
        await callback.message.answer("📂 Какой тип списания?", reply_markup=keyboard)


@router.callback_query(F.data.startswith("w_type:"))
async def choose_type(callback: types.CallbackQuery, state: FSMContext):
    type_id = callback.data.split(":")[1]
    async with async_session() as session:
        result = await session.execute(select(Accounts).where(Accounts.id == type_id))
        ref = result.scalar_one_or_none()

    if not ref:
        return await callback.message.edit_text("❌ Тип списания не найден.")

    await state.update_data(
        payment_type_id=ref.id,
        payment_type_name=ref.name,
        search_msg_id=callback.message.message_id  # 👈 сохраняем id этого сообщения
    )

    await state.set_state(WriteoffStates.Comment)
    await callback.message.edit_text(f"✍️ Укажи причину списания для типа «{ref.name}»:")
    data = await state.get_data()
    await update_writeoff_header(callback.message.bot, callback.message.chat.id, data["header_msg_id"], {
        **data,
        "account_name": ref.name
    })

@router.message(WriteoffStates.Comment)
async def get_comment(message: types.Message, state: FSMContext):
    comment = message.text.strip()
    try:
        await message.delete()
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение: {e}")
    tg_id = str(message.from_user.id)

    async with async_session() as session:
        result = await session.execute(
            select(Employee).where(Employee.telegram_id == tg_id)
        )
        user = result.scalar_one_or_none()
        full_name = f"{user.first_name} {user.last_name}" if user else "Неизвестно"

    await state.update_data(comment=comment + f" | Ввел: {full_name}", items=[])
    await state.set_state(WriteoffStates.AddItems)
    data = await state.get_data()
    msg_id = data.get("search_msg_id")

    if not msg_id:
        # если мы не сохраняли раньше — просто отправим новое сообщение
        msg = await message.answer("🔍 Введите часть названия товара:")
        await state.update_data(search_msg_id=msg.message_id)
        data = await state.get_data()
        await update_writeoff_header(message.bot, message.chat.id, data["header_msg_id"], data)
    else:
        # если сообщение уже есть — редактируем
        try:
            await message.bot.edit_message_text(
                "🔍 Введите часть названия товара:",
                chat_id=message.chat.id,
                message_id=msg_id
            )
        except Exception as e:
            logging.warning(f"Не удалось отредактировать сообщение: {e}")

@router.message(WriteoffStates.AddItems)
async def search_products(message: types.Message, state: FSMContext):
    query = message.text.strip()
    try:
        await message.delete()
    except Exception as e:
        logging.warning(f"Не удалось удалить сообщение: {e}")
    results = await search_nomenclature_for_writeoff(query)
    if not results:
        return await message.answer("🔎 Ничего не найдено.")

    data = await state.get_data()  # 🔧 Добавлено это

    await state.update_data(nomenclature_cache={r['id']: r for r in results})
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=r['name'], callback_data=f"w_item:{r['id']}")]
        for r in results
    ])
    msg_id = data.get("search_msg_id")
    if msg_id:
        try:
            await message.bot.edit_message_text(
                "Выберите товар:",
                chat_id=message.chat.id,
                message_id=msg_id,
                reply_markup=kb
            )
        except Exception as e:
            logging.warning(f"Не удалось отредактировать сообщение: {e}")

@router.callback_query(F.data.startswith("w_item:"))
async def ask_quantity(callback: types.CallbackQuery, state: FSMContext):
    item_id = callback.data.split(":")[1]
    data = await state.get_data()
    item = data.get("nomenclature_cache", {}).get(item_id)
    await state.update_data(current_item=item)
    unit = await get_unit_name_by_id(item["mainunit"])

    # Если кг — спрашиваем граммы, иначе как обычно
    if unit.lower() in ["кг", "kg", "килограмм"]:
        text = f"📏 🖊 Сколько грамм для «{item['name']}»?"
    else:
        text = f"📏 🖊 Сколько {unit} для «{item['name']}»?"

    await state.set_state(WriteoffStates.Quantity)
    await callback.message.edit_text(text)
    await state.update_data(quantity_msg_id=callback.message.message_id)

@router.message(WriteoffStates.Quantity)
async def save_quantity(message: types.Message, state: FSMContext):
    data = await state.get_data()
    try:
        qty = float(message.text.replace(",", "."))
        item = data["current_item"]
        unit = await get_unit_name_by_id(item["mainunit"])

        if unit.lower() in ["кг", "kg", "килограмм"]:
            # Сохраняем для показа граммы (user_quantity), для iiko переводим в кг
            item["user_quantity"] = qty  # граммы
            item["quantity"] = qty / 1000  # для iiko
        else:
            item["user_quantity"] = qty
            item["quantity"] = qty  # всё как было

        items = data["items"]
        items.append(item)
        await state.update_data(items=items)
        try:
            await message.delete()
        except Exception as e:
            logging.warning(f"Не удалось удалить сообщение: {e}")
    except:
        return await message.answer("⚠️ Введите корректное число")

    await state.set_state(WriteoffStates.AddItems)
    msg_id = data.get("quantity_msg_id")
    if msg_id:
        try:
            await message.bot.edit_message_text(
                "🔍 Введите часть названия следующего товара или нажмите «Готово»:",
                chat_id=message.chat.id,
                message_id=msg_id,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Готово", callback_data="w_done")]
                ])
            )
        except Exception as e:
            logging.warning(f"Не удалось отредактировать сообщение: {e}")
        header_id = data.get("header_msg_id")
        if header_id:
            new_data = await state.get_data()
            await update_writeoff_header(message.bot, message.chat.id, header_id, new_data)


@router.callback_query(F.data == "w_more")
async def more_items(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(WriteoffStates.AddItems)
    await callback.message.edit_text("🔍 Введите часть названия товара:")

@router.callback_query(F.data == "w_done")
async def finalize_writeoff(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    required_keys = ("comment", "store_id", "payment_type_id", "items")
    if not all(k in data and data[k] for k in required_keys):
        await callback.message.edit_text(
            "❗ Не удалось завершить акт списания. Возможно, вы не заполнили все поля. Попробуйте начать заново."
        )
        await state.clear()
        return
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

    print("📦 Финальный JSON-документ для iiko:")
    print(document)
    # Подтверждаем пользователю, что нажатие принято и отправка будет в фоне
    try:
        await callback.answer("✅ Запрос принят. Отправка в фоне...")
    except Exception:
        pass

    # Пометим сообщение как 'отправляется' — заменим кнопку на индикатор
    try:
        sending_kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⏳ Отправляется...", callback_data="w_sending")]
        ])
        await callback.message.edit_reply_markup(reply_markup=sending_kb)
    except Exception as e:
        logging.warning(f"Не удалось установить статус 'Отправляется': {e}")

    # Собираем параметры для фоновой отправки
    token = await get_auth_token()
    url = f"{get_base_url()}/resto/api/v2/documents/writeoff"
    params = {"key": token}

    # Снимем копию данных, чтобы можно было безопасно очистить state
    chat_id = callback.message.chat.id
    msg_id = callback.message.message_id
    bot = callback.message.bot
    document_copy = document.copy()

    # Запускаем фональную задачу — отправка и обновление сообщения по результату
    asyncio.create_task(_send_writeoff_task(bot, chat_id, msg_id, url, params, document_copy))

    # Очищаем state, позволяя пользователю начинать новые действия
    await state.clear()


async def _send_writeoff_task(bot: Bot, chat_id: int, msg_id: int, url: str, params: dict, document: dict):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, params=params, json=document)
            response.raise_for_status()

        # Уведомляем пользователя об успехе и меняем кнопку на 'Выполнено'
        try:
            await bot.send_message(chat_id, "✅ Акт списания успешно отправлен в iiko.")
        except Exception as e:
            logging.warning(f"Не удалось отправить сообщение об успешной отправке: {e}")

        try:
            new_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📌 Выполнено", callback_data="w_done_done")]
            ])
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=new_kb)
        except Exception as e:
            logging.warning(f"Не удалось заменить кнопку на 'Выполнено': {e}")

    except httpx.HTTPError as e:
        # При ошибке информируем пользователя и восстанавливаем кнопку 'Готово' для повтора
        try:
            err_text = f"❌ Ошибка при отправке: {e.response.status_code} {e.response.text}"
        except Exception:
            err_text = f"❌ Ошибка при отправке: {e}"
        try:
            await bot.send_message(chat_id, err_text)
        except Exception as e2:
            logging.warning(f"Не удалось отправить сообщение об ошибке: {e2}")

        try:
            retry_kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Готово", callback_data="w_done")]
            ])
            await bot.edit_message_reply_markup(chat_id=chat_id, message_id=msg_id, reply_markup=retry_kb)
        except Exception as e:
            logging.warning(f"Не удалось восстановить кнопку 'Готово': {e}")


@router.callback_query(F.data == "w_done_done")
async def acknowledge_done(callback: types.CallbackQuery):
    # Обработчик для уже выполненной операции — просто подтверждаем, что действие окончено
    await callback.answer("Этот акт уже выполнён.")
