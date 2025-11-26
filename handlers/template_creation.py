
## ────────────── Импорт библиотек и общих функций ──────────────
from aiogram import Bot, Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.dialects.postgresql import insert
from utils.telegram_helpers import edit_or_send
from config import PARENT_FILTERS, STORE_NAME_MAP, ADMIN_IDS
from services.db_queries import DBQueries
from handlers.common import (
    PreparationTemplate,              # Модель шаблона приготовления
    ensure_preparation_table_exists,  # Проверка/создание таблицы шаблонов
    preload_stores,                   # Кэширование складов
    _kbd,                             # Быстрое создание клавиатуры складов
    _get_store_id,                    # Получение id склада по имени
    search_nomenclature,              # Поиск номенклатуры
    search_suppliers,                 # Поиск поставщиков
    STORE_CACHE,                      # Кэш складов
    list_templates,
)
from db.employees_db import async_session
from sqlalchemy import delete
from functools import wraps
import inspect
import logging, pprint
import secrets
from urllib.parse import quote_plus, unquote_plus


## ────────────── Логгер и роутер для aiogram ──────────────
logger = logging.getLogger(__name__)
router = Router()
TEMPLATE_DELETE_TOKENS: dict[int, dict[str, str]] = {}


def _admin_only(func):
    sig = inspect.signature(func)
    has_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

    @wraps(func)
    async def wrapper(event, *args, **kwargs):
        user_id = getattr(event.from_user, "id", None)
        if user_id not in ADMIN_IDS:
            if isinstance(event, types.CallbackQuery):
                await event.answer("Доступ запрещён", show_alert=True)
            else:
                await event.answer("Доступ запрещён")
            return
        filtered_kwargs = kwargs if has_var_kwargs else {k: v for k, v in kwargs.items() if k in sig.parameters}
        return await func(event, *args, **filtered_kwargs)

    return wrapper


def _template_root_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Расходная накладная", callback_data="tpl:invoice")],
        [InlineKeyboardButton(text="📉 Акт списания", callback_data="tpl:writeoff")],
    ])


def _invoice_templates_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Создать шаблон", callback_data="prep:create_template")],
        [InlineKeyboardButton(text="📋 Список шаблонов", callback_data="tpl:invoice:list")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="tpl:root")],
    ])


@router.message(F.text == "Настройка шаблонов")
@_admin_only
async def open_template_settings(message: types.Message):
    await message.answer("Выберите тип шаблонов:", reply_markup=_template_root_keyboard())


@router.callback_query(F.data == "tpl:root")
@_admin_only
async def template_root_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("Выберите тип шаблонов:", reply_markup=_template_root_keyboard())
    await callback.answer()


@router.callback_query(F.data == "tpl:invoice")
@_admin_only
async def template_invoice_menu(callback: types.CallbackQuery):
    await callback.message.edit_text("Настройка шаблонов расходной накладной:", reply_markup=_invoice_templates_keyboard())
    await callback.answer()


@router.callback_query(F.data == "tpl:writeoff")
@_admin_only
async def template_writeoff_menu(callback: types.CallbackQuery):
    await callback.answer("Шаблоны списаний появятся позже", show_alert=True)


async def _render_invoice_template_list(callback: types.CallbackQuery):
    templates = await list_templates()
    if not templates:
        text = "Шаблоны не найдены."
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="tpl:invoice")]])
        await callback.message.edit_text(text, reply_markup=keyboard)
        return

    items = "\n".join(f"• {name}" for name in templates)
    token_map = {secrets.token_hex(3): name for name in templates}
    TEMPLATE_DELETE_TOKENS[callback.from_user.id] = token_map
    buttons = [
        [InlineKeyboardButton(text=f"🗑 {name}", callback_data=f"tpl:invoice:delete:{token}")]
        for token, name in token_map.items()
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="tpl:invoice")])
    await callback.message.edit_text(
        "📋 Список шаблонов:\n" + items,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@router.callback_query(F.data == "tpl:invoice:list")
@_admin_only
async def list_invoice_templates(callback: types.CallbackQuery):
    await _render_invoice_template_list(callback)
    await callback.answer()


@router.callback_query(F.data.startswith("tpl:invoice:delete:"))
@_admin_only
async def delete_invoice_template(callback: types.CallbackQuery):
    token = callback.data.split(":", 3)[-1]
    user_tokens = TEMPLATE_DELETE_TOKENS.get(callback.from_user.id, {})
    template_name = user_tokens.get(token)
    if not template_name:
        template_name = unquote_plus(token)

    async with async_session() as session:
        await session.execute(delete(PreparationTemplate).where(PreparationTemplate.name == template_name))
        await session.commit()

    await callback.answer(f"Шаблон '{template_name}' удалён")
    user_tokens.pop(token, None)
    await _render_invoice_template_list(callback)


## ────────────── Состояния FSM для создания шаблона ──────────────
class TemplateStates(StatesGroup):
    """
    Состояния FSM для пошагового создания шаблона:
    - Name: ввод названия
    - FromStore: выбор склада
    - SelectSupplier: выбор поставщика (ввод текста → поиск)
    - AddItems: добавление позиций
    - SetPrice: ввод цены
    """
    Name = State()            # Ввод названия шаблона
    FromStore = State()       # Выбор склада
    SelectSupplier = State()  # Выбор поставщика (поиск)
    AddItems = State()        # Добавление позиций
    SetPrice = State()        # Ввод цены отгрузки



## ────────────── Вспомогательная функция: отрисовка статуса шаблона ──────────────
async def render_template_status(state: FSMContext, bot: Bot, chat_id: int):
    """
    Обновляет сообщение со статусом текущего шаблона (название, поставщик, позиции)
    """
    """
    Обновляет сообщение со статусом текущего шаблона (название, поставщик, позиции)
    """
    d = await state.get_data()
    items = d.get("template_items", [])
    supplier = d.get("supplier_name", "—")
    store = d.get("from_store_name", "—")
    items_text = (
        "\n".join(
            [
                f"• {it['name']} — {it.get('price','—')} ₽"
                for it in items
            ]
        )
        or "—"
    )
    text = (
        f"📦 <b>Шаблон:</b>\nНазвание: <b>{d.get('template_name','—')}</b>\nСклад: <b>{store}</b>\nПоставщик: <b>{supplier}</b>\n🍕 <b>Позиции:</b>\n{items_text}"
    )
    try:
        await bot.edit_message_text(
            chat_id=chat_id, message_id=d.get("status_message_id"), text=text, parse_mode="HTML"
        )
    except Exception:
        logger.exception("Ошибка обновления статуса шаблона")



## ────────────── Старт создания шаблона ──────────────
@router.callback_query(F.data == "prep:create_template")
async def start_template_creation(c: types.CallbackQuery, state: FSMContext):
    """
    Начало процесса создания шаблона: очищает state, запрашивает название
    """
    """
    Начало процесса создания шаблона: очищает state, запрашивает название
    """
    await state.clear()
    await state.update_data(template_items=[])
    await state.set_state(TemplateStates.Name)
    await c.message.delete()
    status = await c.message.answer("📦 Шаблон\n(заполняется...)")
    msg = await c.message.answer("🛠 Введите название шаблона:")
    await state.update_data(form_message_id=msg.message_id, status_message_id=status.message_id)



## ────────────── Ввод названия шаблона ──────────────
@router.message(TemplateStates.Name)
async def set_template_name(m: types.Message, state: FSMContext):
    """
    Обработка ввода названия шаблона
    """
    await m.delete()
    await state.update_data(template_name=m.text)
    
    # Получаем список складов из БД
    from db.stores_db import Store, async_session
    from sqlalchemy import select
    async with async_session() as s:
        stores = (await s.execute(select(Store.name, Store.id))).all()
    
    if not stores:
        await m.answer("❌ Склады не загружены в БД")
        return
    
    # Создаём клавиатуру со складами
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=f"fromstore:{store_id}")] 
        for name, store_id in stores
    ])
    
    await m.bot.edit_message_text(
        chat_id=m.chat.id,
        message_id=(await state.get_data())["form_message_id"],
        text="📦 С какого склада?",
        reply_markup=kb,
    )
    await state.set_state(TemplateStates.FromStore)
    await render_template_status(state, m.bot, m.chat.id)



## ────────────── Выбор склада ──────────────
@router.callback_query(F.data.startswith("fromstore:"))
async def pick_from_store(c: types.CallbackQuery, state: FSMContext):
    """
    Обработка выбора склада
    """
    store_id = c.data.split(":", 1)[1]
    
    # Получаем имя склада из БД
    from db.stores_db import Store, async_session
    from sqlalchemy import select
    async with async_session() as s:
        result = await s.execute(select(Store.name).where(Store.id == store_id))
        store_name = result.scalar_one_or_none()
    
    if not store_name:
        return await c.answer("❌ Ошибка определения склада")
    
    await state.update_data(
        from_store_id=store_id, 
        from_store_name=store_name,
        to_store_id=store_id,  # для совместимости с invoice XML
        to_store_name=store_name
    )
    await state.set_state(TemplateStates.SelectSupplier)
    await c.message.edit_text("🧾 Для какого поставщика?\nВведите часть названия поставщика:")
    await render_template_status(state, c.bot, c.message.chat.id)
    await c.answer()



## ────────────── Поиск и выбор поставщика ──────────────
@router.message(TemplateStates.SelectSupplier)
async def supplier_search(m: types.Message, state: FSMContext):
    """
    Поиск и выбор поставщика
    """
    q = m.text.strip()
    await m.delete()
    res = await search_suppliers(q)
    if not res:
        return await m.answer("🚫 Поставщик не найден.")
    await state.update_data(supplier_cache={i["id"]: i for i in res})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=i["name"], callback_data=f"selectsupplier:{i['id']}")] for i in res])
    await m.bot.edit_message_text(
        chat_id=m.chat.id, message_id=(await state.get_data())["form_message_id"], text="🔍 Выберите поставщика:", reply_markup=kb
    )



## ────────────── Подтверждение выбора поставщика ──────────────
@router.callback_query(F.data.startswith("selectsupplier:"))
async def select_supplier(c: types.CallbackQuery, state: FSMContext):
    """
    Подтверждение выбора поставщика
    """
    sid = c.data.split(":", 1)[1]
    data = await state.get_data()
    sup = data.get("supplier_cache", {}).get(sid)
    if not sup:
        return await c.answer("❌ Ошибка выбора поставщика")
    await state.update_data(supplier_id=sup["id"], supplier_name=sup["name"])
    await state.set_state(TemplateStates.AddItems)
    await c.message.edit_text("🍕 Что будем готовить?\nВведите часть названия:")
    await c.answer()
    await render_template_status(state, c.bot, c.message.chat.id)



## ────────────── Поиск и добавление позиций ──────────────
@router.message(TemplateStates.AddItems)
async def nomen_search(m: types.Message, state: FSMContext):
    """
    Поиск и добавление позиций
    """
    q = m.text.strip()
    await m.delete()
    res = await search_nomenclature(q)
    if not res:
        return await m.answer("🔍 Ничего не найдено.")
    await state.update_data(nomenclature_cache={i["id"]: i for i in res})
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=i["name"], callback_data=f"additem:{i['id']}")] for i in res])
    await m.bot.edit_message_text(
        chat_id=m.chat.id, message_id=(await state.get_data())["form_message_id"], text="🔎 Найдено:\nВыберите:", reply_markup=kb
    )



## ────────────── Подтверждение добавления позиции ──────────────
@router.callback_query(F.data.startswith("additem:"))
async def add_item(c: types.CallbackQuery, state: FSMContext):
    """
    Подтверждение добавления позиции
    """
    item_id = c.data.split(":", 1)[1]
    data = await state.get_data()
    item = data.get("nomenclature_cache", {}).get(item_id)
    if not item:
        return await c.answer("❌ Товар не найден")
    tpl = data.get("template_items", [])
    tpl.append({"id": item_id, "name": item["name"], "mainunit": item["mainunit"], "quantity": None})
    await state.update_data(template_items=tpl)
    
    # Всегда спрашиваем цену для расходной накладной
    await state.update_data(last_added_item_id=item_id)
    await state.set_state(TemplateStates.SetPrice)
    msg = await c.message.answer(f"💰 Укажите цену отгрузки для «{item['name']}»:")
    await state.update_data(price_msg_id=msg.message_id)
    await c.answer()



## ────────────── Ввод цены отгрузки ──────────────
@router.message(TemplateStates.SetPrice)
async def set_price(m: types.Message, state: FSMContext):
    """
    Ввод цены отгрузки для позиции
    """
    try:
        price = float(m.text.replace(",", "."))
    except ValueError:
        return await m.answer("❌ Введите корректную цену")
    data = await state.get_data()
    items = data.get("template_items", [])
    iid = data.get("last_added_item_id")
    for it in items:
        if it["id"] == iid:
            it["price"] = price
            break
    await state.update_data(template_items=items)
    await m.delete()
    await state.set_state(TemplateStates.AddItems)
    if (pid := data.get("price_msg_id")):
        try:
            await m.bot.delete_message(chat_id=m.chat.id, message_id=pid)
        except Exception:
            logger.exception("remove price msg")
    await m.bot.edit_message_text(
        chat_id=m.chat.id,
        message_id=data.get("form_message_id"),
        text=f"✅ Товар добавлен с ценой.\n\n🍕 Введите название следующей позиции или нажмите 'Готово':",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Готово", callback_data="more:done")]]),
    )
    await render_template_status(state, m.bot, m.chat.id)



## ────────────── Завершение создания шаблона ──────────────
@router.callback_query(F.data == "more:done")
async def finish_template(c: types.CallbackQuery, state: FSMContext):
    """
    Сохраняет шаблон в базу данных, завершает процесс
    """
    """
    Сохраняет шаблон в базу данных, завершает процесс
    """
    data = await state.get_data()
    template = {
        "name": data.get("template_name"),
        "from_store_id": data.get("from_store_id"),
        "to_store_id": data.get("to_store_id"),
        "supplier_id": data.get("supplier_id"),
        "supplier_name": data.get("supplier_name"),
        "items": data.get("template_items", [])
    }
    await c.bot.edit_message_text(chat_id=c.message.chat.id, message_id=data.get("form_message_id"), text="📦 Шаблон сохранён ✅")
    await c.answer("Готово!")
    from db.employees_db import engine
    await ensure_preparation_table_exists(engine)
    async with async_session() as s:
        await s.execute(insert(PreparationTemplate).values(**template).on_conflict_do_nothing())
        await s.commit()
    logger.info("✅ Шаблон сохранён: %s", pprint.pformat(template, width=120))
