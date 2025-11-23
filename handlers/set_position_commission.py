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

from db.position_commission_db import async_session, PositionCommission
from iiko.iiko_auth import get_auth_token, get_base_url
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)
router = Router()

## ────────────── FSM для настройки процента по должности ──────────────
class SetPositionCommissionStates(StatesGroup):
    selecting_position = State()
    entering_percent = State()

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
    """Возвращает словарь {название_должности: процент}"""
    async with async_session() as session:
        result = await session.execute(select(PositionCommission))
        commissions = result.scalars().all()
        return {c.position_name: c.commission_percent for c in commissions}

## ────────────── Сохранение процента в БД ──────────────
async def save_position_commission(position_name: str, percent: float):
    """Сохраняет или обновляет процент комиссии для должности"""
    async with async_session() as session:
        # Проверяем, существует ли запись
        result = await session.execute(
            select(PositionCommission).where(PositionCommission.position_name == position_name)
        )
        existing = result.scalar_one_or_none()
        
        if existing:
            existing.commission_percent = percent
        else:
            new_commission = PositionCommission(
                position_name=position_name,
                commission_percent=percent
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
        current_percent = commissions.get(position_name, 0.0)
        button_text = f"{position_name} — {current_percent}%"
        # Используем индекс вместо названия (короче и безопаснее)
        kb.button(text=button_text, callback_data=f"setpos_{idx}")
    
    kb.adjust(1)  # Одна кнопка на строку
    
    await message.answer(
        "⚙️ <b>Настройка процента комиссии по должностям</b>\n\n"
        "Выберите должность, чтобы установить процент комиссии:",
        reply_markup=kb.as_markup()
    )
    
    await state.set_state(SetPositionCommissionStates.selecting_position)

## ────────────── Обработка выбора должности ──────────────
@router.callback_query(
    StateFilter(SetPositionCommissionStates.selecting_position),
    F.data.startswith("setpos_")
)
async def position_selected(callback: CallbackQuery, state: FSMContext):
    """Обрабатывает выбор должности и запрашивает процент"""
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
    
    # Получаем текущий процент
    commissions = await get_position_commissions()
    current_percent = commissions.get(position_name, 0.0)
    
    await callback.message.edit_text(
        f"⚙️ <b>Настройка комиссии для должности:</b> {position_name}\n\n"
        f"Текущий процент: <b>{current_percent}%</b>\n\n"
        f"Введите новый процент комиссии (0-100):"
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
        
        # Получаем выбранную должность из FSM
        data = await state.get_data()
        position_name = data.get('selected_position')
        
        if not position_name:
            await message.answer("❌ Ошибка: должность не выбрана. Начните заново.")
            await state.clear()
            return
        
        # Сохраняем в БД
        await save_position_commission(position_name, percent)
        
        await message.answer(
            f"✅ <b>Процент комиссии обновлен!</b>\n\n"
            f"Должность: <b>{position_name}</b>\n"
            f"Процент: <b>{percent}%</b>"
        )
        
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Неверный формат. Введите число от 0 до 100:")
