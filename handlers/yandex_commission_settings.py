"""
Handler для настройки процента комиссии Яндекса
"""

import logging
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from db.settings_db import get_yandex_commission, set_yandex_commission

router = Router()
logger = logging.getLogger(__name__)


class YandexCommissionStates(StatesGroup):
    waiting_for_percent = State()


@router.message(F.text == "⚙️ Комиссия Яндекс")
async def start_yandex_commission_setup(message: types.Message, state: FSMContext):
    """
    Начало настройки процента комиссии Яндекса
    """
    current_commission = await get_yandex_commission()
    await message.answer(
        f"💳 *Настройка комиссии Яндекс.Доставки*\n\n"
        f"Текущая комиссия: *{current_commission}%*\n\n"
        f"Введите новый процент комиссии (например, 25.5):",
        parse_mode="Markdown"
    )
    await state.set_state(YandexCommissionStates.waiting_for_percent)


@router.message(YandexCommissionStates.waiting_for_percent)
async def process_yandex_commission(message: types.Message, state: FSMContext):
    """
    Обработка введенного процента комиссии
    """
    try:
        percent = float(message.text.replace(',', '.'))
        
        if percent < 0 or percent > 100:
            await message.answer("❌ Процент должен быть от 0 до 100. Попробуйте еще раз:")
            return
        
        await set_yandex_commission(percent)
        await message.answer(
            f"✅ Комиссия Яндекса установлена: *{percent}%*\n\n"
            f"Теперь при расчете доставки будет учитываться эта комиссия.",
            parse_mode="Markdown"
        )
        await state.clear()
        
    except ValueError:
        await message.answer("❌ Неверный формат. Введите число (например, 25.5):")
