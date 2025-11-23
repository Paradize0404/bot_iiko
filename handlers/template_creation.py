
## ────────────── Импорт библиотек и общих функций ──────────────
from aiogram import Bot, Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy.dialects.postgresql import insert
from utils.telegram_helpers import edit_or_send
from config import PARENT_FILTERS, STORE_NAME_MAP
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
)
from db.employees_db import async_session
import logging, pprint


## ────────────── Логгер и роутер для aiogram ──────────────
logger = logging.getLogger(__name__)
router = Router()



## ────────────── Состояния FSM для создания шаблона ──────────────
class TemplateStates(StatesGroup):
    """
    Состояния FSM для пошагового создания шаблона:
    - Name: ввод названия
    - FromStore: выбор склада-отправителя
    - ToStore: выбор склада-получателя
    - DispatchChoice: выбор, делать ли на отправку
    - SelectSupplier: выбор поставщика
    - AddItems: добавление позиций
    - SetPrice: ввод цены
    """
    Name = State()            # Ввод названия шаблона
    FromStore = State()       # Выбор склада-отправителя
    ToStore = State()         # Выбор склада-получателя
    DispatchChoice = State()  # Выбор: делать ли на отправку
    SelectSupplier = State()  # Выбор поставщика
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
    items_text = (
        "\n".join(
            [
                f"• {it['name']} — {it.get('price','—')} ₽" if d.get("dispatch") else f"• {it['name']}"
                for it in items
            ]
        )
        or "—"
    )
    text = (
        f"📦 <b>Шаблон:</b>\nНазвание: <b>{d.get('template_name','—')}</b>\nПоставщик: <b>{supplier}</b>\n🍕 <b>Позиции:</b>\n{items_text}"
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
    await m.bot.edit_message_text(
        chat_id=m.chat.id,
        message_id=(await state.get_data())["form_message_id"],
        text="📦 С какого склада?",
        reply_markup=_kbd(["Бар", "Кухня"], "fromstore"),
    )
    await render_template_status(state, m.bot, m.chat.id)



## ────────────── Выбор склада-отправителя ──────────────
@router.callback_query(F.data.startswith("fromstore:"))
async def pick_from_store(c: types.CallbackQuery, state: FSMContext):
    """
    Обработка выбора склада-отправителя
    """
    name = c.data.split(":", 1)[1]
    sid = await _get_store_id(name)
    if not sid:
        return await c.answer("❌ Ошибка определения склада")
    await state.update_data(from_store_id=sid, from_store_name=name)
    await state.set_state(TemplateStates.ToStore)
    await c.message.edit_text("🏬 На какой склад?", reply_markup=_kbd(["Бар", "Кухня"], "tostore"))
    await render_template_status(state, c.bot, c.message.chat.id)



## ────────────── Выбор склада-получателя ──────────────
@router.callback_query(F.data.startswith("tostore:"))
async def pick_to_store(c: types.CallbackQuery, state: FSMContext):
    """
    Обработка выбора склада-получателя
    """
    name = c.data.split(":", 1)[1]
    sid = await _get_store_id(name)
    if not sid:
        return await c.answer("❌ Ошибка определения склада")
    await state.update_data(to_store_id=sid, to_store_name=name)
    await state.set_state(TemplateStates.DispatchChoice)
    await c.message.edit_text(
        "✉️ Делаем на отправку?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton("🚚 Да", callback_data="dispatch:yes"), InlineKeyboardButton("📦 Нет", callback_data="dispatch:no")]
            ]
        ),
    )
    await render_template_status(state, c.bot, c.message.chat.id)



## ────────────── Выбор: делать ли на отправку ──────────────
@router.callback_query(F.data.startswith("dispatch:"))
async def dispatch_choice(c: types.CallbackQuery, state: FSMContext):
    """
    Обработка выбора: делать ли на отправку
    """
    dispatch = c.data.split(":", 1)[1] == "yes"
    await state.update_data(dispatch=dispatch)
    if dispatch:
        await state.set_state(TemplateStates.SelectSupplier)
        await c.message.edit_text("🧾 Для кого готовим?\nВведите часть названия поставщика:")
    else:
        await state.set_state(TemplateStates.AddItems)
        await c.message.edit_text("🍕 Что будем готовить?\nВведите часть названия:")
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
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(i["name"], callback_data=f"selectsupplier:{i['id']}")] for i in res])
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
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(i["name"], callback_data=f"additem:{i['id']}")] for i in res])
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
    if data.get("dispatch"):
        await state.update_data(last_added_item_id=item_id)
        await state.set_state(TemplateStates.SetPrice)
        msg = await c.message.answer(f"💰 Укажите цену отгрузки для «{item['name']}»:")
        await state.update_data(price_msg_id=msg.message_id)
        return
    await c.bot.edit_message_text(
        chat_id=c.message.chat.id,
        message_id=data.get("form_message_id"),
        text=f"Добавлен: {item['name']}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("✅ Готово", callback_data="more:done")]]),
    )
    await c.answer()
    await render_template_status(state, c.bot, c.message.chat.id)



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
        text="Товар добавлен с ценой.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton("✅ Готово", callback_data="more:done")]]),
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
    template = {k: data.get(k) for k in ("template_name", "from_store_id", "to_store_id", "supplier_id", "supplier_name")}
    template["items"] = data.get("template_items", [])
    await c.bot.edit_message_text(chat_id=c.message.chat.id, message_id=data.get("form_message_id"), text="📦 Шаблон сохранён ✅")
    await c.answer("Готово!")
    from db.employees_db import engine
    await ensure_preparation_table_exists(engine)
    async with async_session() as s:
        await s.execute(insert(PreparationTemplate).values(**template).on_conflict_do_nothing())
        await s.commit()
    logger.info("✅ Шаблон сохранён: %s", pprint.pformat(template, width=120))
