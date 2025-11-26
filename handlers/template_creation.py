
## ────────────── Импорт библиотек и общих функций ──────────────
from aiogram import Bot, Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.dialects.postgresql import insert
from utils.telegram_helpers import edit_or_send
from config import PARENT_FILTERS, STORE_NAME_MAP, ADMIN_IDS, DOC_CONFIG
from services.db_queries import DBQueries
from handlers.common import (
    PreparationTemplate,              # Модель шаблона приготовления
    ensure_preparation_table_exists,  # Проверка/создание таблицы шаблонов
    WriteoffTemplate,
    ensure_writeoff_template_table_exists,
    preload_stores,                   # Кэширование складов
    _kbd,                             # Быстрое создание клавиатуры складов
    _get_store_id,                    # Получение id склада по имени
    search_nomenclature,              # Поиск номенклатуры
    search_suppliers,                 # Поиск поставщиков
    STORE_CACHE,                      # Кэш складов
    list_templates,
    list_writeoff_templates,
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
WRITEOFF_TEMPLATE_DELETE_TOKENS: dict[int, dict[str, str]] = {}
STORE_PAYMENT_FILTERS = DOC_CONFIG["writeoff"].get("stores", {})


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


def _writeoff_templates_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Создать шаблон", callback_data="wtemplate:create")],
        [InlineKeyboardButton(text="📋 Список шаблонов", callback_data="wtemplate:list")],
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
    await callback.message.edit_text("Настройка шаблонов списаний:", reply_markup=_writeoff_templates_keyboard())
    await callback.answer()


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


async def _render_writeoff_template_list(callback: types.CallbackQuery):
    templates = await list_writeoff_templates()
    if not templates:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="tpl:writeoff")]])
        await callback.message.edit_text("Шаблоны не найдены.", reply_markup=kb)
        return

    items = "\n".join(f"• {name}" for name in templates)
    token_map = {secrets.token_hex(3): name for name in templates}
    WRITEOFF_TEMPLATE_DELETE_TOKENS[callback.from_user.id] = token_map
    buttons = [
        [InlineKeyboardButton(text=f"🗑 {name}", callback_data=f"wtemplate:delete:{token}")]
        for token, name in token_map.items()
    ]
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="tpl:writeoff")])
    await callback.message.edit_text(
        "📋 Список шаблонов списаний:\n" + items,
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


@router.callback_query(F.data == "wtemplate:list")
@_admin_only
async def list_writeoff_templates_handler(callback: types.CallbackQuery):
    await _render_writeoff_template_list(callback)
    await callback.answer()


@router.callback_query(F.data.startswith("wtemplate:delete:"))
@_admin_only
async def delete_writeoff_template_handler(callback: types.CallbackQuery):
    token = callback.data.split(":", 2)[-1]
    user_tokens = WRITEOFF_TEMPLATE_DELETE_TOKENS.get(callback.from_user.id, {})
    template_name = user_tokens.get(token)
    if not template_name:
        template_name = unquote_plus(token)

    async with async_session() as session:
        await session.execute(delete(WriteoffTemplate).where(WriteoffTemplate.name == template_name))
        await session.commit()

    user_tokens.pop(token, None)
    await callback.answer(f"Шаблон '{template_name}' удалён")
    await _render_writeoff_template_list(callback)


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


class WriteoffTemplateStates(StatesGroup):
    Name = State()
    Store = State()
    Account = State()
    Reason = State()
    AddItems = State()


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


async def render_writeoff_template_status(state: FSMContext, bot: Bot, chat_id: int):
    data = await state.get_data()
    items = data.get("writeoff_template_items", [])
    items_text = "\n".join(f"• {it['name']}" for it in items) or "—"
    text = (
        "🧾 <b>Шаблон акта списания</b>\n"
        f"Название: <b>{data.get('writeoff_template_name', '—')}</b>\n"
        f"Склад: <b>{data.get('writeoff_store_name', '—')}</b>\n"
        f"Тип списания: <b>{data.get('writeoff_account_name', '—')}</b>\n"
        f"Причина: <b>{data.get('writeoff_reason', '—')}</b>\n"
        f"🍽 <b>Позиции:</b>\n{items_text}"
    )
    msg_id = data.get("writeoff_status_message_id")
    if not msg_id:
        return
    try:
        await bot.edit_message_text(chat_id=chat_id, message_id=msg_id, text=text, parse_mode="HTML")
    except Exception:
        logger.exception("Ошибка обновления статуса шаблона списания")



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


def _format_writeoff_account_keyboard(type_names: list[str], accounts: list) -> tuple[InlineKeyboardMarkup, dict]:
    by_name = {acc.name: acc for acc in accounts}
    buttons = []
    cache: dict[str, str] = {}
    for name in type_names:
        acc = by_name.get(name)
        if not acc:
            logger.warning("WRITEOFF template account %s отсутствует в БД", name)
            continue
        cache[acc.id] = acc.name
        buttons.append([InlineKeyboardButton(text=acc.name, callback_data=f"wtemplate_account:{acc.id}")])
    if not buttons:
        buttons = [[InlineKeyboardButton(text="Нет доступных типов", callback_data="wtemplate_account:noop")]]
    return InlineKeyboardMarkup(inline_keyboard=buttons), cache


@router.callback_query(F.data == "wtemplate:create")
@_admin_only
async def start_writeoff_template(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(WriteoffTemplateStates.Name)
    await callback.message.delete()
    status = await callback.message.answer("🧾 Шаблон акта списания\n(заполняется...)")
    prompt = await callback.message.answer("🛠 Введите название шаблона:")
    await state.update_data(
        writeoff_template_items=[],
        writeoff_status_message_id=status.message_id,
        writeoff_form_message_id=prompt.message_id,
    )


@router.message(WriteoffTemplateStates.Name)
async def set_writeoff_template_name(message: types.Message, state: FSMContext):
    await message.delete()
    await state.update_data(writeoff_template_name=message.text.strip())

    stores = list(STORE_PAYMENT_FILTERS.keys())
    if not stores:
        await message.answer("❌ В конфиге нет складов для списаний")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=name, callback_data=f"wtemplate_store:{quote_plus(name)}")]
                         for name in stores]
    )
    data = await state.get_data()
    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=data.get("writeoff_form_message_id"),
        text="🏬 С какого склада списывать?",
        reply_markup=kb,
    )
    await state.set_state(WriteoffTemplateStates.Store)
    await render_writeoff_template_status(state, message.bot, message.chat.id)


@router.callback_query(F.data.startswith("wtemplate_store:"))
async def set_writeoff_store(callback: types.CallbackQuery, state: FSMContext):
    store_name = unquote_plus(callback.data.split(":", 1)[1])
    store_id = await _get_store_id(store_name)
    if not store_id:
        await callback.answer("❌ Склад не найден")
        return

    await state.update_data(writeoff_store_id=store_id, writeoff_store_name=store_name)

    type_names = STORE_PAYMENT_FILTERS.get(store_name, [])
    accounts = await DBQueries.get_accounts_by_names(type_names) if type_names else []
    keyboard, cache = _format_writeoff_account_keyboard(type_names, accounts)
    await state.update_data(writeoff_account_cache=cache)

    await state.set_state(WriteoffTemplateStates.Account)
    await callback.message.edit_text("📂 Выберите тип списания:", reply_markup=keyboard)
    await render_writeoff_template_status(state, callback.message.bot, callback.message.chat.id)
    await callback.answer()


@router.callback_query(F.data.startswith("wtemplate_account:"))
async def set_writeoff_account(callback: types.CallbackQuery, state: FSMContext):
    account_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    account_name = data.get("writeoff_account_cache", {}).get(account_id)
    if not account_name:
        await callback.answer("❌ Тип списания недоступен")
        return

    await state.update_data(writeoff_account_id=account_id, writeoff_account_name=account_name)
    await state.set_state(WriteoffTemplateStates.Reason)
    await callback.message.edit_text("📝 Введите причину списания:")
    await render_writeoff_template_status(state, callback.message.bot, callback.message.chat.id)
    await callback.answer()


@router.message(WriteoffTemplateStates.Reason)
async def set_writeoff_reason(message: types.Message, state: FSMContext):
    await message.delete()
    await state.update_data(writeoff_reason=message.text.strip())
    await render_writeoff_template_status(state, message.bot, message.chat.id)
    await state.set_state(WriteoffTemplateStates.AddItems)
    data = await state.get_data()
    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=data.get("writeoff_form_message_id"),
        text="🍽 Введите часть названия позиции:",
    )


@router.message(WriteoffTemplateStates.AddItems)
async def search_writeoff_items(message: types.Message, state: FSMContext):
    query = message.text.strip()
    await message.delete()
    if not query:
        return

    results = await DBQueries.search_nomenclature(
        query,
        types=["GOODS", "PREPARED"],
        parents=None,
        use_parent_filters=False,
    )
    if not results:
        await message.answer("🔎 Ничего не найдено.")
        return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=item["name"], callback_data=f"wtemplate_item:{item['id']}")]
                         for item in results]
    )
    await state.update_data(writeoff_nomenclature_cache={item["id"]: item for item in results})
    data = await state.get_data()
    await message.bot.edit_message_text(
        chat_id=message.chat.id,
        message_id=data.get("writeoff_form_message_id"),
        text="🔎 Найдено. Выберите позицию:",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("wtemplate_item:"))
async def add_item_to_writeoff_template(callback: types.CallbackQuery, state: FSMContext):
    item_id = callback.data.split(":", 1)[1]
    data = await state.get_data()
    cache = data.get("writeoff_nomenclature_cache", {})
    item = cache.get(item_id)
    if not item:
        await callback.answer("❌ Товар не найден")
        return

    items = data.get("writeoff_template_items", [])
    items.append({"id": item_id, "name": item["name"], "mainunit": item.get("mainunit")})
    await state.update_data(writeoff_template_items=items)
    await render_writeoff_template_status(state, callback.message.bot, callback.message.chat.id)

    prompt_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✅ Готово", callback_data="wtemplate_done")]]
    )
    await callback.message.edit_text(
        "✅ Позиция добавлена. Введите новое название или нажмите 'Готово'.",
        reply_markup=prompt_kb,
    )
    await state.update_data(writeoff_nomenclature_cache={})
    await callback.answer()


@router.callback_query(F.data == "wtemplate_done")
async def finish_writeoff_template(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    items = data.get("writeoff_template_items", [])
    if not items:
        await callback.answer("Добавьте хотя бы одну позицию")
        return

    reason = (data.get("writeoff_reason") or "").strip()
    if not reason:
        await callback.answer("Укажите причину списания", show_alert=True)
        return

    template = {
        "name": data.get("writeoff_template_name"),
        "store_id": data.get("writeoff_store_id"),
        "store_name": data.get("writeoff_store_name"),
        "account_id": data.get("writeoff_account_id"),
        "account_name": data.get("writeoff_account_name"),
        "reason": reason,
        "items": items,
    }

    missing_field = next((k for k, v in template.items() if not v), None)
    if missing_field:
        await callback.answer("Заполните все поля перед сохранением", show_alert=True)
        return

    from db.employees_db import engine

    await ensure_writeoff_template_table_exists(engine)
    async with async_session() as session:
        await session.execute(insert(WriteoffTemplate).values(**template).on_conflict_do_nothing())
        await session.commit()

    await callback.message.edit_text("🧾 Шаблон списания сохранён ✅")
    await callback.answer("Готово!")
    logger.info("✅ Writeoff template saved: %s", pprint.pformat(template, width=120))
    await state.clear()
