"""
Обработчик настройки процента комиссии по должностям
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import select
import logging
import httpx

from db.position_commission_db import async_session, PositionCommission, CommissionType, PaymentType
from iiko.iiko_auth import get_auth_token, get_base_url
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)
router = Router()

## ────────────── FSM для настройки процента по должности ──────────────
class SetPositionCommissionStates(StatesGroup):
    selecting_position = State()
    selecting_payment_type = State()  # Выбор типа оплаты (почасовая/посменная/помесячная)
    entering_fixed_rate = State()  # Ввод фиксированной ставки (для посменной/помесячной)
    selecting_commission_type = State()  # Выбор типа комиссии (продажи/расходные)
    entering_percent = State()  # Ввод процента

## ────────────── Получение справочника должностей из iiko ──────────────
async def get_positions_dict_from_iiko() -> dict:
    """Получает справочник должностей: {код: полное_название}"""
    try:
        token = await get_auth_token()
        base_url = get_base_url()
        
        # Получаем список должностей
        roles_url = f"{base_url}/resto/api/employees/roles"
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            response = await client.get(
                roles_url,
                headers={"Cookie": f"key={token}"}
            )
        
        response.raise_for_status()
        roles_xml = response.text
        
        logger.info(f"📥 Получен XML ролей, длина: {len(roles_xml)} символов")
        
        root = ET.fromstring(roles_xml)
        positions_dict = {}
        
        # Парсим роли: <role><code>CO1</code><name>Повар</name></role>
        roles = root.findall('.//role')
        logger.info(f"👔 Найдено ролей в XML: {len(roles)}")
        
        for role in roles:
            code = role.findtext('code')
            name = role.findtext('name')
            if code and name:
                positions_dict[code] = name
                logger.debug(f"Роль: {code} = {name}")
        
        logger.info(f"✅ Загружено {len(positions_dict)} должностей")
        return positions_dict
    except Exception as e:
        logger.exception(f"❌ Ошибка получения должностей из iiko: {e}")
        return {}


async def get_positions_from_iiko():
    """Получает список всех должностей (полные названия)"""
    positions_dict = await get_positions_dict_from_iiko()
    # Возвращаем полные названия
    return sorted(list(positions_dict.values()))

## ────────────── Получение текущих процентов из БД ──────────────
async def get_position_commissions():
    """Возвращает словарь {название_должности: (payment_type, fixed_rate, commission_percent, commission_type)}"""
    async with async_session() as session:
        result = await session.execute(select(PositionCommission))
        commissions = result.scalars().all()
        return {
            c.position_name: (c.payment_type, c.fixed_rate, c.commission_percent, c.commission_type) 
            for c in commissions
        }

## ────────────── Сохранение настроек в БД ──────────────
async def save_position_commission(position_name: str, payment_type: str, fixed_rate: float, 
                                   percent: float, commission_type: str):
    """Сохраняет или обновляет настройки комиссии для должности"""
    async with async_session() as session:
        # Проверяем, существует ли запись
        result = await session.execute(
            select(PositionCommission).where(PositionCommission.position_name == position_name)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            existing.payment_type = payment_type
            existing.fixed_rate = fixed_rate
            existing.commission_percent = percent
            existing.commission_type = commission_type
        else:
            new_commission = PositionCommission(
                position_name=position_name,
                payment_type=payment_type,
                fixed_rate=fixed_rate,
                commission_percent=percent,
                commission_type=commission_type
            )
            session.add(new_commission)
        
        await session.commit()

## ────────────── Главное меню настройки комиссий ──────────────
@router.message(F.text == "⚙️ Настройка комиссий")
async def position_commission_menu(message: Message, state: FSMContext):
    """Показывает список всех должностей с текущими процентами"""
    position_names = await get_positions_from_iiko()
    
    if not position_names:
        await message.answer("❌ Не удалось загрузить список должностей из iiko")
        return
    
    commissions = await get_position_commissions()
    
    # Сохраняем список должностей в FSM для использования по индексу
    await state.update_data(positions_list=position_names)
    
    # Создаем клавиатуру с должностями
    kb = InlineKeyboardBuilder()
    for idx, position_name in enumerate(position_names):
        commission_data = commissions.get(position_name)
        if commission_data:
            payment_type, fixed_rate, current_percent, comm_type = commission_data
            
            # Определяем иконки
            if payment_type == "hourly":
                payment_emoji = "⏰"
                rate_text = f"{current_percent}%"
            elif payment_type == "per_shift":
                payment_emoji = "📅"
                rate_text = f"{fixed_rate}₽/смену + {current_percent}%"
            else:  # monthly
                payment_emoji = "📆"
                rate_text = f"{fixed_rate}₽/мес + {current_percent}%"
            
            type_emoji = "💰" if comm_type == "sales" else "📦"
            button_text = f"{position_name} — {payment_emoji} {rate_text} {type_emoji}"
        else:
            button_text = f"{position_name} — не настроено"
        # Используем индекс вместо названия (короче и безопаснее)
        kb.button(text=button_text, callback_data=f"setpos_{idx}")
    
    kb.adjust(1)  # Одна кнопка на строку
    
    await message.answer(
        "⚙️ <b>Настройка комиссий по должностям</b>\n\n"
        "<b>Легенда:</b>\n"
        "⏰ — Почасовая (из iiko)\n"
        "📅 — Посменная (фикс. ставка)\n"
        "📆 — Помесячная (фикс. ставка)\n"
        "💰 — Комиссия от продаж\n"
        "📦 — Комиссия от расходных накладных\n\n"
        "Выберите должность для настройки:",
        reply_markup=kb.as_markup()
    )
    
    await state.set_state(SetPositionCommissionStates.selecting_position)

## ────────────── Обработка выбора должности ──────────────
@router.callback_query(
    StateFilter(SetPositionCommissionStates.selecting_position),
    F.data.startswith("setpos_")
)
async def position_selected(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор должности и запрашивает тип оплаты"""
    # Получаем индекс из callback_data
    idx = int(callback.data.replace("setpos_", ""))
    
    # Получаем список должностей из FSM
    data = await state.get_data()
    positions_list = data.get('positions_list', [])
    
    if idx >= len(positions_list):
        await callback.answer("❌ Ошибка: должность не найдена", show_alert=True)
        return
    
    position_name = positions_list[idx]
    
    # Сохраняем выбранную должность в FSM
    await state.update_data(selected_position=position_name)
    
    # Получаем текущие настройки
    commissions = await get_position_commissions()
    commission_data = commissions.get(position_name)
    
    if commission_data:
        payment_type, fixed_rate, current_percent, current_comm_type = commission_data
        
        # Формируем текст текущих настроек
        if payment_type == "hourly":
            payment_text = "Почасовая ⏰ (из iiko)"
            rate_info = ""
        elif payment_type == "per_shift":
            payment_text = "Посменная 📅"
            rate_info = f"\nСтавка за смену: <b>{fixed_rate}₽</b>"
        else:  # monthly
            payment_text = "Помесячная 📆"
            rate_info = f"\nСтавка за месяц: <b>{fixed_rate}₽</b>"
        
        comm_text = "продаж 💰" if current_comm_type == "sales" else "расходных накладных 📦"
        
        current_info = (
            f"<b>Текущие настройки:</b>\n"
            f"Тип оплаты: {payment_text}{rate_info}\n"
            f"Комиссия: <b>{current_percent}%</b> от {comm_text}\n\n"
        )
    else:
        current_info = "Комиссия не настроена\n\n"
    
    # Создаем клавиатуру выбора типа оплаты
    kb = InlineKeyboardBuilder()
    kb.button(text="⏰ Почасовая (из iiko)", callback_data="payment_hourly")
    kb.button(text="📅 Посменная", callback_data="payment_per_shift")
    kb.button(text="📆 Помесячная", callback_data="payment_monthly")
    kb.adjust(1)
    
    await callback.message.edit_text(
        f"⚙️ <b>Настройка комиссии: {position_name}</b>\n\n"
        f"{current_info}"
        f"<b>Шаг 1/3:</b> Выберите тип оплаты:",
        reply_markup=kb.as_markup()
    )
    
    await state.set_state(SetPositionCommissionStates.selecting_payment_type)
    await callback.answer()

## ────────────── Обработка выбора типа оплаты ──────────────
@router.callback_query(
    StateFilter(SetPositionCommissionStates.selecting_payment_type),
    F.data.startswith("payment_")
)
async def payment_type_selected(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор типа оплаты"""
    payment_type = callback.data.replace("payment_", "")
    
    # Сохраняем тип оплаты в FSM
    await state.update_data(payment_type=payment_type)
    
    data = await state.get_data()
    position_name = data.get('selected_position')
    
    # Если почасовая - сразу переходим к выбору типа комиссии
    if payment_type == "hourly":
        await state.update_data(fixed_rate=None)
        
        # Создаем клавиатуру выбора типа комиссии
        kb = InlineKeyboardBuilder()
        kb.button(text="💰 От продаж", callback_data="commtype_sales")
        kb.button(text="📦 От расходных накладных", callback_data="commtype_writeoff")
        kb.adjust(1)
        
        await callback.message.edit_text(
            f"⚙️ <b>Настройка комиссии: {position_name}</b>\n\n"
            f"Тип оплаты: <b>Почасовая ⏰</b> (ставка берется из iiko)\n\n"
            f"<b>Шаг 2/3:</b> Выберите тип комиссии:",
            reply_markup=kb.as_markup()
        )
        
        await state.set_state(SetPositionCommissionStates.selecting_commission_type)
    else:
        # Для посменной/помесячной - запрашиваем фиксированную ставку
        payment_text = "смену" if payment_type == "per_shift" else "месяц"
        emoji = "📅" if payment_type == "per_shift" else "📆"
        
        await callback.message.edit_text(
            f"⚙️ <b>Настройка комиссии: {position_name}</b>\n\n"
            f"Тип оплаты: <b>{'Посменная' if payment_type == 'per_shift' else 'Помесячная'} {emoji}</b>\n\n"
            f"<b>Шаг 2/4:</b> Введите фиксированную ставку за {payment_text} (в рублях):"
        )
        
        await state.set_state(SetPositionCommissionStates.entering_fixed_rate)
    
    await callback.answer()

## ────────────── Обработка ввода фиксированной ставки ──────────────
@router.message(StateFilter(SetPositionCommissionStates.entering_fixed_rate))
async def fixed_rate_entered(message: Message, state: FSMContext):
    """Обрабатывает ввод фиксированной ставки"""
    try:
        # Валидация ввода
        fixed_rate = float(message.text.replace(',', '.').replace(' ', ''))
        
        if fixed_rate <= 0:
            await message.answer("❌ Ставка должна быть больше 0. Попробуйте снова:")
            return
        
        # Сохраняем ставку
        await state.update_data(fixed_rate=fixed_rate)
        
        data = await state.get_data()
        position_name = data.get('selected_position')
        payment_type = data.get('payment_type')
        
        payment_text = "смену" if payment_type == "per_shift" else "месяц"
        emoji = "📅" if payment_type == "per_shift" else "📆"
        
        # Создаем клавиатуру выбора типа комиссии
        kb = InlineKeyboardBuilder()
        kb.button(text="💰 От продаж", callback_data="commtype_sales")
        kb.button(text="📦 От расходных накладных", callback_data="commtype_writeoff")
        kb.adjust(1)
        
        await message.answer(
            f"⚙️ <b>Настройка комиссии: {position_name}</b>\n\n"
            f"Тип оплаты: <b>{'Посменная' if payment_type == 'per_shift' else 'Помесячная'} {emoji}</b>\n"
            f"Ставка: <b>{fixed_rate}₽</b> за {payment_text}\n\n"
            f"<b>Шаг 3/4:</b> Выберите тип комиссии:",
            reply_markup=kb.as_markup()
        )
        
        await state.set_state(SetPositionCommissionStates.selecting_commission_type)
    
    except ValueError:
        await message.answer("❌ Неверный формат. Введите число (например: 5000 или 5000.50):")

## ────────────── Обработка выбора типа комиссии ──────────────
@router.callback_query(
    StateFilter(SetPositionCommissionStates.selecting_commission_type),
    F.data.startswith("commtype_")
)
async def commission_type_selected(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор типа комиссии и запрашивает процент"""
    commission_type = "sales" if callback.data == "commtype_sales" else "writeoff"
    type_text = "продаж" if commission_type == "sales" else "расходных накладных"
    
    # Сохраняем тип в FSM
    await state.update_data(commission_type=commission_type)
    
    data = await state.get_data()
    position_name = data.get('selected_position')
    payment_type = data.get('payment_type')
    fixed_rate = data.get('fixed_rate')
    
    # Формируем текст о типе оплаты
    if payment_type == "hourly":
        payment_info = "Тип оплаты: <b>Почасовая ⏰</b> (из iiko)\n"
        step_text = "Шаг 3/3:"
    elif payment_type == "per_shift":
        payment_info = f"Тип оплаты: <b>Посменная 📅</b>\nСтавка: <b>{fixed_rate}₽</b> за смену\n"
        step_text = "Шаг 4/4:"
    else:  # monthly
        payment_info = f"Тип оплаты: <b>Помесячная 📆</b>\nСтавка: <b>{fixed_rate}₽</b> за месяц\n"
        step_text = "Шаг 4/4:"
    
    await callback.message.edit_text(
        f"⚙️ <b>Настройка комиссии: {position_name}</b>\n\n"
        f"{payment_info}"
        f"Тип комиссии: <b>{type_text}</b>\n\n"
        f"<b>{step_text}</b> Введите процент комиссии (0-100):"
    )
    
    await state.set_state(SetPositionCommissionStates.entering_percent)
    await callback.answer()

## ────────────── Обработка ввода процента ──────────────
@router.message(StateFilter(SetPositionCommissionStates.entering_percent))
async def percent_entered(message: Message, state: FSMContext):
    """Обрабатывает введенный процент и сохраняет в БД"""
    try:
        # Валидация ввода
        percent = float(message.text.replace(',', '.'))
        
        if not (0 <= percent <= 100):
            await message.answer("❌ Процент должен быть от 0 до 100. Попробуйте снова:")
            return
        
        # Получаем данные из FSM
        data = await state.get_data()
        position_name = data.get('selected_position')
        payment_type = data.get('payment_type', "hourly")
        fixed_rate = data.get('fixed_rate')
        commission_type = data.get('commission_type', "sales")
        
        if not position_name:
            await message.answer("❌ Ошибка: должность не выбрана. Начните заново.")
            await state.clear()
            return
        
        # Сохраняем в БД
        await save_position_commission(position_name, payment_type, fixed_rate, percent, commission_type)
        
        # Формируем текст об оплате
        if payment_type == "hourly":
            payment_info = "Тип оплаты: <b>Почасовая ⏰</b> (из iiko)\n"
        elif payment_type == "per_shift":
            payment_info = f"Тип оплаты: <b>Посменная 📅</b>\nСтавка: <b>{fixed_rate}₽</b> за смену\n"
        else:  # monthly
            payment_info = f"Тип оплаты: <b>Помесячная 📆</b>\nСтавка: <b>{fixed_rate}₽</b> за месяц\n"
        
        type_text = "продаж" if commission_type == "sales" else "расходных накладных"
        type_emoji = "💰" if commission_type == "sales" else "📦"
        
        await message.answer(
            f"✅ <b>Настройки комиссии сохранены!</b>\n\n"
            f"Должность: <b>{position_name}</b>\n"
            f"{payment_info}"
            f"Комиссия: <b>{percent}%</b> от {type_text} {type_emoji}"
        )
        
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Неверный формат. Введите число от 0 до 100:")
