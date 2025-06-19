import asyncpg
import pprint
from aiogram import Bot
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import StatesGroup, State
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import String, select, JSON, inspect
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, String
from db.employees_db import async_session
from sqlalchemy.dialects.postgresql import insert

from utils.telegram_helpers import edit_or_send  # Для редактирования сообщений

router = Router()

Base = declarative_base()

STORE_CACHE = {}

class PreparationTemplate(Base):
    __tablename__ = "preparation_templates"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    from_store_id: Mapped[str] = mapped_column(String)
    to_store_id: Mapped[str] = mapped_column(String)
    supplier_id: Mapped[str] = mapped_column(String, nullable=True)
    supplier_name: Mapped[str] = mapped_column(String, nullable=True)
    items: Mapped[dict] = mapped_column(JSON)



class Store(Base):
    __tablename__ = "stores"

    id = Column(String, primary_key=True)
    name = Column(String)
    code = Column(String)
    type = Column(String)

class Nomenclature(Base):
    __tablename__ = "nomenclature"

    id = Column(String, primary_key=True)
    name = Column(String)
    parent = Column(String)
    mainunit = Column("mainunit", String)
    type = Column(String)


class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(String, primary_key=True)
    code = Column(String)
    name = Column(String)



STORE_NAME_MAP = {
    "Бар": ["Бар Пиццерия"],
    "Кухня": ["Кухня Пиццерия"],
    "Кондитерский": ["Кондитерский Пиццерия"],
    "Реализация": ["Реализация Пиццерия"],  # если появится
}



PARENT_FILTERS = [
    '4d2a8e1d-7c24-4df1-a8bd-58a6e2e82a12',
    '6c5f1595-ce55-459d-b368-94bab2f20ee3'  # Да, они одинаковые — ты это указал
]

async def ensure_preparation_table_exists(engine: AsyncEngine):
    async with engine.begin() as conn:
        def check_tables(sync_conn):
            inspector = inspect(sync_conn)
            return inspector.get_table_names()

        tables = await conn.run_sync(check_tables)

        if "preparation_templates" not in tables:
            await conn.run_sync(Base.metadata.create_all)
            print("✅ Таблица preparation_templates создана")
        else:
            print("ℹ️ Таблица preparation_templates уже существует")

async def preload_stores():
    global STORE_CACHE
    async with async_session() as session:
        result = await session.execute(select(Store.name, Store.id))
        for name, store_id in result.all():
            STORE_CACHE[name.strip()] = store_id


def get_store_keyboard(variants: list[str], prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"{prefix}:{name}")]
        for name in variants
    ])



async def get_store_id_by_name(user_input_name: str) -> str | None:
    valid_names = STORE_NAME_MAP.get(user_input_name.strip())
    if not valid_names:
        return None

    for db_name in valid_names:
        if db_name.strip() in STORE_CACHE:
            return STORE_CACHE[db_name.strip()]
    return None


async def search_nomenclature(partial_name: str) -> list[dict]:
    async with async_session() as session:
        terms = [t.strip() for t in partial_name.lower().split() if t.strip()]
        if not terms:
            return []

        query = select(Nomenclature.id, Nomenclature.name, Nomenclature.mainunit).limit(50)

        # ✅ Фильтр по допустимым родителям
        query = query.where(Nomenclature.parent.in_(PARENT_FILTERS))

        # ✅ Фильтрация по всем частям запроса
        for term in terms:
            query = query.where(Nomenclature.name.ilike(f"%{term}%"))

        result = await session.execute(query)
        rows = result.all()
        return [{"id": r.id, "name": r.name, "mainunit": r.mainunit} for r in rows]



async def search_suppliers(partial_name: str) -> list[dict]:
    async with async_session() as session:
        terms = [t.strip().lower() for t in partial_name.split() if t.strip()]
        if not terms:
            return []

        query = select(Supplier.id, Supplier.name).limit(50)
        for term in terms:
            query = query.where(Supplier.name.ilike(f"%{term}%"))

        result = await session.execute(query)
        rows = result.all()
        return [{"id": r.id, "name": r.name} for r in rows]



# 🔄 Состояния FSM
class TemplateStates(StatesGroup):
    Name = State()
    FromStore = State()
    ToStore = State()
    SelectSupplier = State()
    AddItems = State()
    SetPrice = State() 



async def render_template_status(state: FSMContext, bot: Bot, chat_id: int):
    data = await state.get_data()
    msg_id = data.get("status_message_id")

    name = data.get("template_name", "—")
    from_store = data.get("from_store_name", "—")
    to_store = data.get("to_store_name", "—")
    supplier = data.get("supplier_name", "—")  # ✅ Вот эта строка — добавь
    items = data.get("template_items", [])

    items_text = "\n".join([
        f"• {item['name']} — {item.get('price', '—')} ₽" if data.get("to_store_name") == "Реализация" else f"• {item['name']}"
        for item in items
    ]) or "—"

    text = (
        f"📦 <b>Шаблон:</b>\n"
        f"Название: <b>{name}</b>\n"
        f"Склад ➡️: <code>{from_store}</code>\n"
        f"Склад ⬅️: <code>{to_store}</code>\n"
        f"Поставщик: <b>{supplier}</b>\n"
        f"🍕 <b>Позиции:</b>\n{items_text}"
    )

    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="HTML")
    except Exception as e:
        print(f"[!] Ошибка обновления шаблона: {e}")

# 🛠️ Начало создания шаблона
@router.callback_query(F.data == "prep:create_template")
async def start_template_creation(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await state.update_data(template_items=[])

    # отправляем новое сообщение и сохраняем его id
    

    await state.set_state(TemplateStates.Name)

    # удаляем предыдущее сообщение с кнопкой
    await callback.message.delete()
    status = await callback.message.answer("📦 Шаблон\n(заполняется...)")
    msg = await callback.message.answer("🛠 Введите название шаблона:")
    await state.update_data(
        form_message_id=msg.message_id,
        status_message_id=status.message_id,
        template_items=[]
    )

# 1️⃣ Название шаблона
@router.message(TemplateStates.Name)
async def get_template_name(message: types.Message, state: FSMContext):
    await message.delete()  # 🧼 Удаляем сообщение с введённым названием

    await state.update_data(template_name=message.text)
    msg_id = (await state.get_data())['form_message_id']

    keyboard = get_store_keyboard(["Бар", "Кухня", "Кондитерский"], prefix="fromstore")
    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=msg_id,
        text="📦 С какого склада?",
        reply_markup=keyboard
    )
    await render_template_status(state, message.bot, message.chat.id)


@router.callback_query(F.data.startswith("fromstore:"))
async def handle_from_store_choice(callback: types.CallbackQuery, state: FSMContext):
    store_name = callback.data.split(":")[1]
    store_id = await get_store_id_by_name(store_name)
    if not store_id:
        return await callback.answer("❌ Ошибка определения склада")

    # 👇 Сохраняем и id, и понятное название
    await state.update_data(
        from_store_id=store_id,
        from_store_name=store_name
    )

    keyboard = get_store_keyboard(["Бар", "Кухня", "Реализация"], prefix="tostore")
    await state.set_state(TemplateStates.ToStore)
    await callback.message.edit_text("🏬 На какой склад?", reply_markup=keyboard)

    await render_template_status(state, callback.bot, callback.message.chat.id)


@router.callback_query(F.data.startswith("tostore:"))
async def handle_to_store_choice(callback: types.CallbackQuery, state: FSMContext):
    store_name = callback.data.split(":")[1]
    store_id = await get_store_id_by_name(store_name)
    if not store_id:
        return await callback.answer("❌ Ошибка определения склада")

    await state.update_data(to_store_id=store_id, to_store_name=store_name)

    if store_name == "Реализация":
        await state.set_state(TemplateStates.SelectSupplier)
        await callback.message.edit_text("🧾 Для кого готовим?\nВведите часть названия поставщика:")
    else:
        await state.set_state(TemplateStates.AddItems)
        await callback.message.edit_text("🍕 Что будем готовить?\nВведите часть названия:")

    await render_template_status(state, callback.bot, callback.message.chat.id)

@router.message(TemplateStates.SelectSupplier)
async def handle_supplier_search(message: types.Message, state: FSMContext):
    query = message.text.strip()
    await message.delete()

    results = await search_suppliers(query)
    if not results:
        return await message.answer("🚫 Поставщик не найден. Попробуйте другое название.")

    await state.update_data(supplier_cache={item['id']: item for item in results})

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=item['name'], callback_data=f"selectsupplier:{item['id']}")]
        for item in results
    ])

    msg_id = (await state.get_data())['form_message_id']
    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=msg_id,
        text="🔍 Выберите поставщика:",
        reply_markup=keyboard
    )

@router.callback_query(F.data.startswith("selectsupplier:"))
async def handle_supplier_select(callback: types.CallbackQuery, state: FSMContext):
    supplier_id = callback.data.split(":")[1]
    data = await state.get_data()
    supplier = data.get("supplier_cache", {}).get(supplier_id)

    if not supplier:
        return await callback.answer("❌ Ошибка выбора поставщика")

    await state.update_data(
        supplier_id=supplier["id"],
        supplier_name=supplier["name"]
    )

    await state.set_state(TemplateStates.AddItems)
    await callback.message.edit_text("🍕 Что будем готовить?\nВведите часть названия:")
    await callback.answer()

    await render_template_status(state, callback.bot, callback.message.chat.id)


# 4️⃣ Что будем готовить (поиск)
@router.message(TemplateStates.AddItems)
async def handle_nomenclature_search(message: types.Message, state: FSMContext):
    query = message.text.strip()
    await message.delete()  # удаляем сообщение пользователя

    results = await search_nomenclature(query)
    if not results:
        return await message.answer("🔍 Ничего не найдено. Попробуйте другую часть названия.")

    await state.update_data(nomenclature_cache={item['id']: item for item in results})

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=item['name'], callback_data=f"additem:{item['id']}")]
        for item in results
    ])  # 👈 добавляем готово

    msg_id = (await state.get_data())['form_message_id']
    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=msg_id,
        text="🔎 Найдено:\nВыберите из списка:",
        reply_markup=keyboard
    )

# ➕ Добавление товара
@router.callback_query(F.data.startswith("additem:"))
async def add_item(callback: types.CallbackQuery, state: FSMContext):
    item_id = callback.data.split(":")[1]
    data = await state.get_data()
    item = data.get("nomenclature_cache", {}).get(item_id)

    if not item:
        return await callback.answer("❌ Ошибка: товар не найден")

    data['template_items'].append({
        'id': item_id,
        'name': item['name'],
        'mainunit': item['mainunit'],
        'quantity': None
    })
    await state.update_data(template_items=data['template_items'])
     # Если склад "Реализация" — спрашиваем цену
    if data.get("to_store_name") == "Реализация":
        await state.update_data(last_added_item_id=item_id)
        await state.set_state(TemplateStates.SetPrice)
        msg = await callback.message.answer(f"💰 Укажите цену отгрузки для «{item['name']}»:")
        await state.update_data(price_msg_id=msg.message_id)
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        
        [InlineKeyboardButton(text="✅ Готово", callback_data="more:done")]
    ])
    msg_id = data.get("form_message_id")
    await callback.bot.edit_message_text(
        chat_id=callback.message.chat.id,
        message_id=msg_id,
        text=f"Добавлен: {item['name']}\nМожешь ввести ещё или нажми «Готово».",
        reply_markup=kb
    )
    await callback.answer()
    await render_template_status(state, callback.bot, callback.message.chat.id)

@router.message(TemplateStates.SetPrice)
async def handle_set_price(message: types.Message, state: FSMContext):
    try:
        price = float(message.text.replace(",", "."))
    except ValueError:
        return await message.answer("❌ Введите корректную цену (например, 199.99)")

    data = await state.get_data()
    items = data.get("template_items", [])
    item_id = data.get("last_added_item_id")

    for item in items:
        if item["id"] == item_id:
            item["price"] = price  # 👈 сохраняем цену
            break

    await state.update_data(template_items=items)
    price_msg_id = data.get("price_msg_id")
    if price_msg_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=price_msg_id)
        except Exception as e:
            print(f"⚠️ Не удалось удалить сообщение с запросом цены: {e}")

    await message.delete()

    # Возврат в AddItems
    await state.set_state(TemplateStates.AddItems)

    msg_id = data.get("form_message_id")
    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=msg_id,
        text="Товар добавлен с ценой. Можешь ввести ещё или нажать «Готово».",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ Готово", callback_data="more:done")]]
        )
    )

    await render_template_status(state, message.bot, message.chat.id)

# # ⏳ Кнопка "Готово" пока в заглушке
# @router.callback_query(F.data == "more:done")
# async def finish_template(callback: types.CallbackQuery, state: FSMContext):
#     data = await state.get_data()

#     template = {
#         "name": data.get("template_name"),
#         "from_store_id": data.get("from_store_id"),
#         "to_store_id": data.get("to_store_id"),
#         "supplier_id": data.get("supplier_id"),       # ✅ добавлено
#         "supplier_name": data.get("supplier_name"),   # ✅ добавлено
#         "items": data.get("template_items", [])
#     }

#     # 🖨️ Вывод в консоль
#     print("📋 Готовый шаблон:")
#     print(template)

#     msg_id = data.get("form_message_id")
#     await callback.bot.edit_message_text(
#         chat_id=callback.message.chat.id,
#         message_id=msg_id,
#         text="📦 Шаблон собран. (в консоли — ✅)"
#     )
#     await state.clear()
#     await callback.answer()



@router.callback_query(F.data == "more:done")
async def finish_template(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()

    template = {
        "name": data.get("template_name"),
        "from_store_id": data.get("from_store_id"),
        "to_store_id": data.get("to_store_id"),
        "supplier_id": data.get("supplier_id"),
        "supplier_name": data.get("supplier_name"),
        "items": data.get("template_items", []),
    }

    # Отображаем пользователю, что шаблон сохранён
    msg_id = data.get("form_message_id")
    await callback.bot.edit_message_text(
        chat_id=callback.message.chat.id,
        message_id=msg_id,
        text="📦 Шаблон сохранён ✅\n(в консоли подробности)"
    )
    await callback.answer("Готово!")

    # Сохраняем в базу
    from db.employees_db import engine
    await ensure_preparation_table_exists(engine)

    async with async_session() as session:
        await session.execute(
            insert(PreparationTemplate).values(**template).on_conflict_do_nothing()
        )
        await session.commit()

    print("✅ Шаблон сохранён в базу данных PostgreSQL:")
    pprint.pprint(template, width=120)