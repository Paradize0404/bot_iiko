from aiogram import Router, F
from aiogram.types import Message
from aiogram.fsm.context import FSMContext

from states import DocumentFlow
from utils.db_stores import fetch_by_names
from utils.stores_kb import make_keyboard
from keyboards.main_keyboard import document_menu_keyboard, main_menu_keyboard
from utils.telegram_helpers import tidy_response

router = Router()

WAREHOUSE_NAME_MAP = {
    "Кухня": "Кухня Пиццерия",
    "Кондитерский": "Кондитерский Пиццерия",
    "Бар": "Бар Пиццерия",
    "Расходные материалы": "Расх. мат. Пиццерия",
}
PREP_WAREHOUSES  = ("Кухня", "Бар", "Кондитерский")
WRITE_WAREHOUSES = ("Кухня", "Бар", "Кондитерский", "Расходные материалы")
MOVE_WAREHOUSES  = ("Кухня", "Бар", "Кондитерский")

def render_document_progress(data: dict) -> str:
    doc_map = {
        "prep": "Акт приготовления",
        "writeoff": "Акт списания",
        "move": "Внутреннее перемещение",
    }
    parts = [f"📄 Тип документа: <b>{doc_map.get(data.get('doc_type'), '—')}</b>"]
    if "src_name" in data:
        parts.append(f"🏷️ Склад-источник: <b>{data['src_name']}</b>")
    if "dst_name" in data:
        parts.append(f"📥 Склад-приёмник: <b>{data['dst_name']}</b>")
    return "\n".join(parts)

@router.message(F.text == "Создание документа")
async def show_doc_menu(msg: Message) -> None:
    await msg.answer("Выберите тип документа:", reply_markup=document_menu_keyboard())

@router.message(F.text == "🧾 Акт приготовления")
async def prep_start(msg: Message, state: FSMContext):
    await _ask_source(
        msg,
        state,
        doc_type="prep",
        warehouse_names=PREP_WAREHOUSES,
        answer_text="🧾 Акт приготовления.\n<b>Шаг 1 / 2.</b> Выберите склад-источник:",
    )

@router.message(F.text == "📉 Акт списания")
async def writeoff_start(msg: Message, state: FSMContext):
    await _ask_source(
        msg,
        state,
        doc_type="writeoff",
        warehouse_names=WRITE_WAREHOUSES,
        answer_text="📉 Акт списания.\nВыберите склад, с которого списываем:",
        need_destination=False,
    )

@router.message(F.text == "🔄 Внутреннее перемещение")
async def move_start(msg: Message, state: FSMContext):
    await _ask_source(
        msg,
        state,
        doc_type="move",
        warehouse_names=MOVE_WAREHOUSES,
        answer_text="🔄 Внутреннее перемещение.\n<b>Шаг 1 / 2.</b> С какого склада перемещаем?",
    )

@router.message(DocumentFlow.picking_src)
async def picked_source(msg: Message, state: FSMContext):
    data = await state.get_data()
    warehouses = data["warehouses"]
    name2id = {name: id_ for id_, name in warehouses}

    if msg.text not in name2id:
        await msg.answer("Не узнаю такой склад. Попробуйте ещё раз.")
        return

    await state.update_data(src_id=name2id[msg.text], src_name=msg.text)

    doc_msg_id = data["doc_msg_id"]
    progress_text = render_document_progress(await state.get_data())
    await msg.bot.edit_message_text(
        chat_id=msg.chat.id,
        message_id=doc_msg_id,
        text=progress_text,
        parse_mode="HTML"
    )

    if not data.get("need_destination"):
        await _finish_document(msg, state)
        return

    dst_rows = [(id_, name) for id_, name in warehouses if name != msg.text]
    kb = make_keyboard(dst_rows)
    await state.set_state(DocumentFlow.picking_dst)
    sent = await tidy_response(
        trigger=msg,
        text="<b>Шаг 2 / 2.</b> На какой склад перемещаем?",
        old_msg_id=data.get("question_msg_id"),
        reply_markup=kb,
        parse_mode="HTML"
    )
    await state.update_data(question_msg_id=sent.message_id)

@router.message(DocumentFlow.picking_dst)
async def picked_destination(msg: Message, state: FSMContext):
    data = await state.get_data()
    name2id = {name: id_ for id_, name in data["warehouses"]}

    if msg.text not in name2id:
        await msg.answer("Не узнаю такой склад. Попробуйте ещё раз.")
        return

    await state.update_data(dst_id=name2id[msg.text], dst_name=msg.text)

    doc_msg_id = data["doc_msg_id"]
    progress_text = render_document_progress(await state.get_data())
    await msg.bot.edit_message_text(
        chat_id=msg.chat.id,
        message_id=doc_msg_id,
        text=progress_text,
        parse_mode="HTML"
    )

    await _finish_document(msg, state)

async def _ask_source(
    msg: Message,
    state: FSMContext,
    *,
    doc_type: str,
    warehouse_names: tuple[str, ...],
    answer_text: str,
    need_destination: bool = True,
):
    db_names = [WAREHOUSE_NAME_MAP.get(name, name) for name in warehouse_names]
    rows = await fetch_by_names(db_names)
    dbname_to_ui = {v: k for k, v in WAREHOUSE_NAME_MAP.items()}
    rows = [(id_, dbname_to_ui.get(name, name)) for id_, name in rows]

    if not rows:
        await msg.answer("❌ В БД не найдено ни одного склада из списка.")
        return

    await state.set_state(DocumentFlow.picking_src)
    progress_text = render_document_progress({"doc_type": doc_type})
    sent = await msg.answer(progress_text, parse_mode="HTML")

    kb = make_keyboard(rows)
    sent_question = await msg.answer(answer_text, reply_markup=kb, parse_mode="HTML")

    await state.update_data(
        warehouses=rows,
        doc_type=doc_type,
        need_destination=need_destination,
        doc_msg_id=sent.message_id,
        question_msg_id=sent_question.message_id
    )

async def _finish_document(msg: Message, state: FSMContext) -> None:
    data = await state.get_data()

    # 🍕 Заменяем сообщение вопроса
    sent = await tidy_response(
        trigger=msg,
        text="🍕 Что будем готовить?",
        old_msg_id=data.get("question_msg_id"),
        reply_markup=None,
        parse_mode="HTML"
    )

    # 👇 Обновляем message_id, если дальше будешь использовать
    await state.update_data(question_msg_id=sent.message_id)

    # 🧼 Очищаем состояние после всех операций
    await state.clear()