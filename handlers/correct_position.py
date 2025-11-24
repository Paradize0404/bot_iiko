"""
Обработчик ручной корректировки должности сотрудника
Позволяет задать должность с определенной даты
"""
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import InlineKeyboardBuilder
import httpx
import xml.etree.ElementTree as ET
import logging
from datetime import date, datetime, timedelta
from db.employee_position_history_db import set_employee_position, get_position_history_for_period
from iiko.iiko_auth import get_auth_token, get_base_url

logger = logging.getLogger(__name__)
router = Router()


## ────────────── FSM для корректировки должности ──────────────
class CorrectPositionStates(StatesGroup):
    selecting_employee = State()
    entering_date = State()
    selecting_position = State()


## ────────────── Получение списка сотрудников ──────────────
async def get_employees_list_from_iiko() -> dict:
    """Получает список активных сотрудников из iiko"""
    try:
        token = await get_auth_token()
        base_url = get_base_url()
        
        url = f"{base_url}/resto/api/employees"
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            response = await client.get(
                url,
                headers={"Cookie": f"key={token}"},
                params={"includeDeleted": "false"}
            )
        response.raise_for_status()
        
        tree = ET.fromstring(response.text)
        employees = {}
        
        for emp in tree.findall(".//employee"):
            emp_id = emp.findtext("id")
            emp_name = emp.findtext("name", "Неизвестно")
            
            if emp_id and emp.findtext("deleted", "false") != "true":
                employees[emp_id] = emp_name
        
        return employees
    except Exception as e:
        logger.error(f"Ошибка получения сотрудников: {e}")
        return {}


## ────────────── Получение списка должностей ──────────────
async def get_positions_from_iiko() -> dict:
    """Получает список должностей из iiko"""
    try:
        token = await get_auth_token()
        base_url = get_base_url()
        
        url = f"{base_url}/resto/api/employees/roles"
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            response = await client.get(
                url,
                headers={"Cookie": f"key={token}"}
            )
        response.raise_for_status()
        
        tree = ET.fromstring(response.text)
        roles = {}
        
        for role in tree.findall(".//role"):
            code = role.findtext("code")
            name = role.findtext("name")
            if code and name:
                roles[name] = name
        
        return roles
    except Exception as e:
        logger.error(f"Ошибка получения должностей: {e}")
        return {}


## ────────────── Главное меню корректировки должности ──────────────
@router.message(Command("correct_position"))
@router.message(F.text == "📝 Корректировка должности")
async def start_position_correction(message: Message, state: FSMContext):
    """Начало процесса корректировки должности"""
    employees = await get_employees_list_from_iiko()
    
    if not employees:
        await message.answer("❌ Не удалось загрузить список сотрудников")
        return
    
    # Сохраняем список сотрудников
    await state.update_data(employees=employees)
    
    # Создаем клавиатуру с сотрудниками
    kb = InlineKeyboardBuilder()
    for idx, (emp_id, emp_name) in enumerate(sorted(employees.items(), key=lambda x: x[1])):
        kb.button(text=emp_name, callback_data=f"corr_emp_{idx}")
    
    kb.adjust(2)  # Две кнопки в ряд
    
    await message.answer(
        "📝 <b>Корректировка должности сотрудника</b>\n\n"
        "Выберите сотрудника:",
        reply_markup=kb.as_markup()
    )
    
    await state.set_state(CorrectPositionStates.selecting_employee)


## ────────────── Выбор сотрудника ──────────────
@router.callback_query(
    StateFilter(CorrectPositionStates.selecting_employee),
    F.data.startswith("corr_emp_")
)
async def employee_selected(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора сотрудника"""
    idx = int(callback.data.replace("corr_emp_", ""))
    
    data = await state.get_data()
    employees = data.get('employees', {})
    employees_list = list(employees.items())
    
    if idx >= len(employees_list):
        await callback.answer("❌ Ошибка выбора сотрудника", show_alert=True)
        return
    
    emp_id, emp_name = employees_list[idx]
    
    # Сохраняем выбранного сотрудника
    await state.update_data(selected_emp_id=emp_id, selected_emp_name=emp_name)
    
    # Показываем текущую историю должностей
    today = date.today()
    history = await get_position_history_for_period(emp_id, today - timedelta(days=90), today)
    
    if history:
        history_lines = []
        for h in history:
            from_date = h['valid_from'].strftime('%d.%m.%Y')
            if h['valid_to'] and h['valid_to'] >= today:
                to_date = "по сегодня"
            elif h['valid_to']:
                to_date = f"по {h['valid_to'].strftime('%d.%m.%Y')}"
            else:
                to_date = "по н.в."
            history_lines.append(f"  • {h['position_name']}: с {from_date} {to_date}")
        history_text = "\n".join(history_lines)
    else:
        history_text = "  История пуста"
    
    await callback.message.edit_text(
        f"📝 <b>Корректировка должности</b>\n\n"
        f"Сотрудник: <b>{emp_name}</b>\n\n"
        f"<b>Текущая история (последние 90 дней):</b>\n{history_text}\n\n"
        f"Введите дату, с которой меняется должность (в формате ДД.МM.ГГГГ):\n"
        f"Например: 15.11.2025"
    )
    
    await state.set_state(CorrectPositionStates.entering_date)
    await callback.answer()


## ────────────── Ввод даты ──────────────
@router.message(StateFilter(CorrectPositionStates.entering_date))
async def date_entered(message: Message, state: FSMContext):
    """Обработка введенной даты"""
    try:
        # Парсим дату
        effective_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
        
        # Проверяем, что дата не в будущем
        if effective_date > date.today():
            await message.answer("❌ Дата не может быть в будущем. Попробуйте еще раз:")
            return
        
        # Сохраняем дату
        await state.update_data(effective_date=effective_date)
        
        # Получаем список должностей
        positions = await get_positions_from_iiko()
        
        if not positions:
            await message.answer("❌ Не удалось загрузить список должностей")
            await state.clear()
            return
        
        # Сохраняем должности
        await state.update_data(positions=positions)
        
        # Создаем клавиатуру с должностями
        kb = InlineKeyboardBuilder()
        for idx, position_name in enumerate(sorted(positions.keys())):
            kb.button(text=position_name, callback_data=f"corr_pos_{idx}")
        
        kb.adjust(1)  # Одна кнопка в ряд
        
        data = await state.get_data()
        emp_name = data.get('selected_emp_name')
        
        await message.answer(
            f"📝 <b>Корректировка должности</b>\n\n"
            f"Сотрудник: <b>{emp_name}</b>\n"
            f"Дата изменения: <b>{effective_date.strftime('%d.%m.%Y')}</b>\n\n"
            f"Выберите новую должность:",
            reply_markup=kb.as_markup()
        )
        
        await state.set_state(CorrectPositionStates.selecting_position)
    
    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте ДД.ММ.ГГГГ (например: 15.11.2025)")


## ────────────── Выбор должности ──────────────
@router.callback_query(
    StateFilter(CorrectPositionStates.selecting_position),
    F.data.startswith("corr_pos_")
)
async def position_selected(callback: CallbackQuery, state: FSMContext):
    """Обработка выбора должности и сохранение"""
    idx = int(callback.data.replace("corr_pos_", ""))
    
    data = await state.get_data()
    positions = data.get('positions', {})
    positions_list = sorted(positions.keys())
    
    if idx >= len(positions_list):
        await callback.answer("❌ Ошибка выбора должности", show_alert=True)
        return
    
    position_name = positions_list[idx]
    emp_id = data.get('selected_emp_id')
    emp_name = data.get('selected_emp_name')
    effective_date = data.get('effective_date')
    
    # Сохраняем изменение в БД
    try:
        await set_employee_position(emp_id, emp_name, position_name, effective_date)
        
        await callback.message.edit_text(
            f"✅ <b>Должность успешно обновлена!</b>\n\n"
            f"Сотрудник: <b>{emp_name}</b>\n"
            f"Новая должность: <b>{position_name}</b>\n"
            f"С даты: <b>{effective_date.strftime('%d.%m.%Y')}</b>\n\n"
            f"История периодов автоматически пересчитана."
        )
        
        await state.clear()
        await callback.answer("✅ Сохранено")
    
    except Exception as e:
        logger.exception(f"Ошибка при сохранении должности: {e}")
        await callback.message.edit_text(f"❌ Ошибка при сохранении: {e}")
        await state.clear()
