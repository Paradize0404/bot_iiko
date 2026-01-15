## ────────────── Импорт библиотек и общих функций ──────────────
import logging
import pprint
import re
from datetime import datetime
from html import escape
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Optional
from aiogram import Router, types, F
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, FSInputFile
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
from services.db_queries import DBQueries

try:
    from fpdf import FPDF  # type: ignore
except Exception:  # library might be missing in legacy envs
    FPDF = None  # type: ignore

try:
    from unidecode import unidecode  # type: ignore
except Exception:  # noqa: BLE001
    unidecode = None  # type: ignore

router = Router()
logger = logging.getLogger(__name__)


FONT_BUNDLE_PATH = Path(__file__).resolve().parent.parent / "fonts" / "DejaVuSans.ttf"


async def _ensure_font_file() -> Optional[Path]:
    if FONT_BUNDLE_PATH.exists():
        return FONT_BUNDLE_PATH
    try:
        FONT_BUNDLE_PATH.parent.mkdir(parents=True, exist_ok=True)
        urls = [
            "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/version_2_37/ttf/DejaVuSans.ttf",
            "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans.ttf",
            "https://github.com/dejavu-fonts/dejavu-fonts/raw/version_2_37/ttf/DejaVuSans.ttf",
        ]
        import httpx

        async with httpx.AsyncClient(timeout=20) as client:
            for url in urls:
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    FONT_BUNDLE_PATH.write_bytes(resp.content)
                    logger.info("Скачан шрифт DejaVuSans.ttf из %s", url)
                    return FONT_BUNDLE_PATH
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Не удалось скачать шрифт из %s: %s", url, exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось скачать шрифт DejaVuSans: %s", exc)
    return None


async def _find_font_path() -> Optional[Path]:
    bundled = await _ensure_font_file()
    candidates = [
        bundled,
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
    ]
    for path in candidates:
        if not path:
            continue
        font_path = Path(path)
        if font_path.exists():
            return font_path
    return None


def _safe_text(text: str | None, allow_unicode: bool) -> str:
    if not text:
        return "-"
    if allow_unicode:
        return text
    if unidecode:
        try:
            translit = unidecode(text)
            safe = translit.encode("ascii", "ignore").decode("ascii", "ignore")
            return safe or "-"
        except Exception:  # noqa: BLE001
            pass
    safe = text.encode("ascii", "ignore").decode("ascii", "ignore")
    return safe or "-"


def _fit_text(pdf: "FPDF", text: str, width: float) -> str:
    padding = 2
    if pdf.get_string_width(text) <= width - padding:
        return text
    truncated = text
    while truncated and pdf.get_string_width(truncated + "...") > width - padding:
        truncated = truncated[:-1]
    return (truncated + "...") if truncated else text[:1]


def _build_pdf_filename(template_name: str | None) -> str:
    base = template_name or "расходная накладная"
    safe = re.sub(r"[^0-9A-Za-zА-Яа-я _.-]", "_", base).strip()
    if not safe:
        safe = "расходная накладная"
    return f"{safe}.pdf"


async def _generate_invoice_pdf(doc: dict, unit_names: dict[str, str]) -> Path | None:
    if FPDF is None:
        logger.warning("FPDF не установлен, PDF не будет сгенерирован")
        return None

    items = doc.get("items", []) or []
    font_path = await _find_font_path()
    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    font_family = "Arial"
    unicode_enabled = False
    if font_path:
        try:
            pdf.add_font("DocFont", "", str(font_path), uni=True)
            font_family = "DocFont"
            unicode_enabled = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось подключить шрифт %s: %s", font_path, exc)

    page_height = 297.0
    margin_top = 8.0
    margin_bottom = 8.0
    usable_height = page_height - margin_top - margin_bottom
    per_copy_height = usable_height / 2.0

    row_h_base = 8.0
    title_h_base = row_h_base + 2.0
    info_lines = 5  # шаблон, склад, сотрудник, дата, поставщик
    info_h_base = info_lines * row_h_base
    spacer_base = 2.0
    table_h_base = (len(items) + 1) * row_h_base  # +1 за заголовок таблицы
    total_row_base = row_h_base
    signature_h_base = row_h_base + 2.0
    needed_height_base = (
        title_h_base
        + info_h_base
        + spacer_base
        + table_h_base
        + total_row_base
        + signature_h_base
    )

    scale = min(1.0, per_copy_height / needed_height_base) if needed_height_base else 1.0
    row_h = max(4.0, row_h_base * scale)
    title_h = title_h_base * scale
    info_h = row_h_base * scale
    spacer_h = spacer_base * scale
    signature_h = signature_h_base * scale
    font_factor = max(0.65, scale)

    title_size = max(10, round(14 * font_factor))
    info_size = max(8, round(10 * font_factor))
    table_size = max(7, round(9 * font_factor))

    headers = ["№", "Позиция", "Кол-во", "Ед.", "Цена", "Сумма"]
    widths = [10, 80, 25, 20, 25, 30]

    def _render_copy(y_offset: float) -> None:
        pdf.set_xy(10, y_offset)
        pdf.set_font(font_family, size=title_size)
        pdf.cell(0, title_h, _safe_text("Расходная накладная", unicode_enabled), ln=1)

        pdf.set_font(font_family, size=info_size)
        pdf.cell(0, info_h, _safe_text(f"Шаблон: {doc.get('name')}", unicode_enabled), ln=1)
        pdf.cell(0, info_h, _safe_text(f"Склад: {doc.get('store_name') or '—'}", unicode_enabled), ln=1)
        pdf.cell(0, info_h, _safe_text(f"Сотрудник: {doc.get('user_fullname') or '—'}", unicode_enabled), ln=1)
        pdf.cell(0, info_h, _safe_text(f"Дата: {doc.get('created_at') or '—'}", unicode_enabled), ln=1)
        pdf.cell(0, info_h, _safe_text(f"Поставщик: {doc.get('supplier_name') or '—'}", unicode_enabled), ln=1)
        pdf.ln(spacer_h)

        pdf.set_font(font_family, size=table_size)
        for title, width in zip(headers, widths):
            pdf.cell(width, row_h, _safe_text(title, unicode_enabled), border=1, align="C")
        pdf.ln(row_h)

        total_sum = 0.0
        for idx, item in enumerate(items, start=1):
            qty = float(item.get("quantity") or 0)
            price = float(item.get("price") or 0)
            subtotal = qty * price
            total_sum += subtotal

            unit = unit_names.get(item.get("mainunit"), "шт")
            name_text = _fit_text(pdf, _safe_text(item.get("name") or "-", unicode_enabled), widths[1])
            qty_text = f"{qty:.3f}".rstrip("0").rstrip(".")
            pdf.cell(widths[0], row_h, str(idx), border=1, align="C")
            pdf.cell(widths[1], row_h, name_text, border=1)
            pdf.cell(widths[2], row_h, qty_text, border=1, align="R")
            pdf.cell(widths[3], row_h, _safe_text(unit, unicode_enabled), border=1, align="C")
            pdf.cell(widths[4], row_h, f"{price:.2f}", border=1, align="R")
            pdf.cell(widths[5], row_h, f"{subtotal:.2f}", border=1, align="R")
            pdf.ln(row_h)

        pdf.cell(sum(widths[:-1]), row_h, _safe_text("Итого", unicode_enabled), border=1, align="R")
        pdf.cell(widths[-1], row_h, f"{total_sum:.2f}", border=1, align="R")

        pdf.ln(signature_h)
        pdf.cell(sum(widths), row_h, _safe_text("Принял: ______________________", unicode_enabled), border=0, align="L")

    _render_copy(margin_top)
    _render_copy(margin_top + per_copy_height)

    tmp = NamedTemporaryFile(delete=False, suffix=".pdf")
    pdf.output(tmp.name)
    return Path(tmp.name)


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

    # Имя сотрудника по Telegram ID
    employee_name = (callback.from_user.full_name or "").strip() or "—"
    tg_id_str = str(callback.from_user.id)
    try:
        user = await DBQueries.get_employee_by_telegram(tg_id_str)
        if user:
            employee_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or employee_name
        else:
            user_full = await DBQueries.get_user_fullname_by_telegram(tg_id_str)
            if user_full:
                employee_name = user_full.strip() or employee_name
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось получить имя сотрудника: %s", exc)

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
        user_fullname=employee_name,
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
        'store_name': data.get('store_name'),
        'user_fullname': data.get('user_fullname'),
        'created_at': datetime.now().strftime("%Y-%m-%d %H:%M"),
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
    unit_names = data.get('unit_names', {})
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

    pdf_path = await _generate_invoice_pdf(
        {**final, "store_name": final.get("store_name") or data.get("store_name")},
        unit_names,
    )
    if pdf_path:
        try:
            await callback.message.answer_document(
                FSInputFile(pdf_path, filename=_build_pdf_filename(final.get('name'))),
                caption="📄 PDF расходной накладной",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось отправить PDF накладной: %s", exc)
        finally:
            try:
                pdf_path.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001
                logger.debug("Не удалось удалить временный PDF %s", pdf_path)

    # Отправляем накладную
    inv_xml = build_invoice_xml(final)
    ok, resp = await post_xml('/resto/api/documents/import/outgoingInvoice', inv_xml)

    if ok:
        await callback.message.edit_text("✅ Расходная накладная успешно отправлена в iiko!")
    else:
        await callback.message.edit_text(f"❌ Ошибка при отправке накладной:\n<pre>{escape(resp)}</pre>", parse_mode='HTML')

    await state.clear()
    await callback.answer()
