"""
Handler для управления цехами (отделами) и привязки должностей
"""
import logging
from aiogram import Router, F, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from db.departments_db import (
    get_all_departments,
    get_department_positions,
    get_all_department_positions,
    get_available_positions,
    add_position_to_department,
    remove_position_from_department,
    get_position_department
)

router = Router()
logger = logging.getLogger(__name__)


class DepartmentStates(StatesGroup):
    """Состояния для управления цехами"""
    pass


@router.message(F.text == "⚙️ Настройка цехов")
async def show_departments_menu(message: types.Message):
    """
    Показать меню управления цехами
    """
    departments = await get_all_departments()
    dept_data = await get_all_department_positions()
    
    text = "🏭 *УПРАВЛЕНИЕ ЦЕХАМИ*\n\n"
    text += "Выберите цех для управления должностями:\n\n"
    
    for dept in departments:
        positions = dept_data.get(dept, [])
        count = len(positions)
        text += f"• {dept}: {count} должностей\n"
    
    # Создаем кнопки для каждого цеха (используем индексы)
    keyboard = []
    for idx, dept in enumerate(departments):
        keyboard.append([InlineKeyboardButton(
            text=f"🏭 {dept}",
            callback_data=f"dept_manage:{idx}"
        )])
    
    keyboard.append([InlineKeyboardButton(
        text="🔙 Назад",
        callback_data="dept_back"
    )])
    
    await message.answer(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("dept_manage:"))
async def manage_department(callback: CallbackQuery):
    """
    Управление конкретным цехом
    """
    await callback.answer()
    
    # Получаем индекс цеха и преобразуем в название
    dept_idx = int(callback.data.split(":")[1])
    departments = await get_all_departments()
    
    if dept_idx >= len(departments):
        await callback.answer("❌ Ошибка: цех не найден", show_alert=True)
        return
    
    department = departments[dept_idx]
    positions = await get_department_positions(department)
    available = await get_available_positions()
    
    text = f"🏭 *{department}*\n\n"
    
    if positions:
        text += "📋 *Должности в цехе:*\n"
        for pos in positions:
            text += f"• {pos}\n"
    else:
        text += "📋 В цехе пока нет должностей\n"
    
    text += f"\n✅ Доступно для добавления: {len(available)} должностей"
    
    keyboard = [
        [InlineKeyboardButton(
            text="➕ Добавить должность",
            callback_data=f"dept_add:{dept_idx}"
        )],
    ]
    
    if positions:
        keyboard.append([InlineKeyboardButton(
            text="➖ Удалить должность",
            callback_data=f"dept_remove:{dept_idx}"
        )])
    
    keyboard.append([InlineKeyboardButton(
        text="🔙 К списку цехов",
        callback_data="dept_list"
    )])
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("dept_add:"))
async def add_position_menu(callback: CallbackQuery, state: FSMContext):
    """
    Показать список доступных должностей для добавления
    """
    await callback.answer()
    
    # Получаем индекс цеха
    dept_idx = int(callback.data.split(":")[1])
    departments = await get_all_departments()
    department = departments[dept_idx]
    available = await get_available_positions()
    
    if not available:
        await callback.answer("❌ Нет свободных должностей", show_alert=True)
        return
    
    # Сохраняем список должностей в state для использования по индексу
    await state.update_data(
        dept_available_positions=available,
        dept_current_department=department,
        dept_current_idx=dept_idx  # Сохраняем индекс тоже
    )
    
    text = f"🏭 *{department}*\n\n"
    text += "Выберите должность для добавления:\n"
    
    keyboard = []
    
    # Используем индексы вместо полных названий в callback_data
    for idx, pos in enumerate(available[:20]):  # Ограничиваем 20 должностями
        keyboard.append([InlineKeyboardButton(
            text=pos,
            callback_data=f"dept_add_idx:{idx}"
        )])
    
    if len(available) > 20:
        text += f"\n_Показано первых 20 из {len(available)}_"
    
    keyboard.append([InlineKeyboardButton(
        text="🔙 Назад",
        callback_data=f"dept_manage:{dept_idx}"
    )])
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("dept_add_idx:"))
async def confirm_add_position(callback: CallbackQuery, state: FSMContext):
    """
    Подтверждение добавления должности в цех
    """
    await callback.answer()
    
    # Получаем данные из state
    data = await state.get_data()
    available = data.get('dept_available_positions', [])
    department = data.get('dept_current_department', '')
    dept_idx = data.get('dept_current_idx', 0)
    
    # Получаем индекс должности
    idx = int(callback.data.split(":")[1])
    
    if idx >= len(available):
        await callback.answer("❌ Ошибка: должность не найдена", show_alert=True)
        return
    
    position = available[idx]
    
    # Добавляем должность
    success = await add_position_to_department(department, position)
    
    if success:
        await callback.answer(f"✅ Должность '{position}' добавлена в {department}", show_alert=True)
    else:
        existing_dept = await get_position_department(position)
        await callback.answer(
            f"❌ Должность уже в цехе '{existing_dept}'",
            show_alert=True
        )
    
    # Очищаем state
    await state.clear()
    
    # Получаем обновленные данные и показываем меню цеха
    positions = await get_department_positions(department)
    available = await get_available_positions()
    
    text = f"🏭 *{department}*\n\n"
    
    if positions:
        text += "📋 *Должности в цехе:*\n"
        for pos in positions:
            text += f"• {pos}\n"
    else:
        text += "📋 В цехе пока нет должностей\n"
    
    text += f"\n✅ Доступно для добавления: {len(available)} должностей"
    
    keyboard = [
        [InlineKeyboardButton(
            text="➕ Добавить должность",
            callback_data=f"dept_add:{dept_idx}"
        )],
    ]
    
    if positions:
        keyboard.append([InlineKeyboardButton(
            text="➖ Удалить должность",
            callback_data=f"dept_remove:{dept_idx}"
        )])
    
    keyboard.append([InlineKeyboardButton(
        text="🔙 К списку цехов",
        callback_data="dept_list"
    )])
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("dept_remove:"))
async def remove_position_menu(callback: CallbackQuery, state: FSMContext):
    """
    Показать список должностей в цехе для удаления
    """
    await callback.answer()
    
    # Получаем индекс цеха
    dept_idx = int(callback.data.split(":")[1])
    departments = await get_all_departments()
    department = departments[dept_idx]
    positions = await get_department_positions(department)
    
    if not positions:
        await callback.answer("❌ В цехе нет должностей", show_alert=True)
        return
    
    # Сохраняем в state
    await state.update_data(
        dept_positions_to_remove=positions,
        dept_current_department=department,
        dept_current_idx=dept_idx
    )
    
    text = f"🏭 *{department}*\n\n"
    text += "Выберите должность для удаления:\n"
    
    keyboard = []
    
    # Используем индексы
    for idx, pos in enumerate(positions):
        keyboard.append([InlineKeyboardButton(
            text=f"❌ {pos}",
            callback_data=f"dept_rm_idx:{idx}"
        )])
    
    keyboard.append([InlineKeyboardButton(
        text="🔙 Назад",
        callback_data=f"dept_manage:{dept_idx}"
    )])
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("dept_rm_idx:"))
async def confirm_remove_position(callback: CallbackQuery, state: FSMContext):
    """
    Подтверждение удаления должности из цеха
    """
    await callback.answer()
    
    # Получаем данные из state
    data = await state.get_data()
    positions = data.get('dept_positions_to_remove', [])
    department = data.get('dept_current_department', '')
    dept_idx = data.get('dept_current_idx', 0)
    
    # Получаем индекс должности
    idx = int(callback.data.split(":")[1])
    
    if idx >= len(positions):
        await callback.answer("❌ Ошибка: должность не найдена", show_alert=True)
        return
    
    position = positions[idx]
    
    # Удаляем должность
    success = await remove_position_from_department(position)
    
    if success:
        await callback.answer(f"✅ Должность '{position}' удалена из цеха", show_alert=True)
    else:
        await callback.answer("❌ Ошибка удаления", show_alert=True)
    
    # Очищаем state
    await state.clear()
    
    # Получаем обновленные данные и показываем меню цеха
    positions = await get_department_positions(department)
    available = await get_available_positions()
    
    text = f"🏭 *{department}*\n\n"
    
    if positions:
        text += "📋 *Должности в цехе:*\n"
        for pos in positions:
            text += f"• {pos}\n"
    else:
        text += "📋 В цехе пока нет должностей\n"
    
    text += f"\n✅ Доступно для добавления: {len(available)} должностей"
    
    keyboard = [
        [InlineKeyboardButton(
            text="➕ Добавить должность",
            callback_data=f"dept_add:{dept_idx}"
        )],
    ]
    
    if positions:
        keyboard.append([InlineKeyboardButton(
            text="➖ Удалить должность",
            callback_data=f"dept_remove:{dept_idx}"
        )])
    
    keyboard.append([InlineKeyboardButton(
        text="🔙 К списку цехов",
        callback_data="dept_list"
    )])
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "dept_list")
async def back_to_departments_list(callback: CallbackQuery):
    """
    Вернуться к списку цехов
    """
    await callback.answer()
    
    departments = await get_all_departments()
    dept_data = await get_all_department_positions()
    
    text = "🏭 *УПРАВЛЕНИЕ ЦЕХАМИ*\n\n"
    text += "Выберите цех для управления должностями:\n\n"
    
    for dept in departments:
        positions = dept_data.get(dept, [])
        count = len(positions)
        text += f"• {dept}: {count} должностей\n"
    
    keyboard = []
    for idx, dept in enumerate(departments):
        keyboard.append([InlineKeyboardButton(
            text=f"🏭 {dept}",
            callback_data=f"dept_manage:{idx}"
        )])
    
    keyboard.append([InlineKeyboardButton(
        text="🔙 Назад",
        callback_data="dept_back"
    )])
    
    await callback.message.edit_text(
        text,
        reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard),
        parse_mode="Markdown"
    )


@router.callback_query(F.data == "dept_back")
async def close_departments_menu(callback: CallbackQuery):
    """
    Закрыть меню цехов
    """
    await callback.answer()
    await callback.message.delete()
