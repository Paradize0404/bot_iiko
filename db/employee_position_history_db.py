"""
История должностей сотрудников
Отслеживает изменения должностей во времени для правильного расчета зарплаты
"""
import os
from dotenv import load_dotenv
import logging
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy import Column, String, Date, text, select, and_, or_
from datetime import date, datetime, timedelta

## ────────────── Загрузка переменных окружения и настройка БД ──────────────
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise ValueError("DATABASE_URL is not set!")

Base = declarative_base()

## ────────────── Модель истории должностей ──────────────
class EmployeePositionHistory(Base):
    __tablename__ = 'employee_position_history'

    id = Column(String, primary_key=True)  # UUID
    employee_id = Column(String, nullable=False, index=True)  # ID сотрудника из iiko
    employee_name = Column(String, nullable=False)  # ФИО для удобства
    position_name = Column(String, nullable=False)  # Название должности
    valid_from = Column(Date, nullable=False, index=True)  # С какой даты действует
    valid_to = Column(Date, nullable=True, index=True)  # До какой даты (NULL = по текущий день)
    
    # Индекс для быстрого поиска активных записей
    __table_args__ = (
        {'postgresql_ignore_search_path': True}
    )

## ────────────── Логгер и подключение к БД ──────────────
logger = logging.getLogger(__name__)
engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

## ────────────── Инициализация таблицы ──────────────
async def init_employee_position_history_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        logger.info("📦 Таблица employee_position_history создана или существует.")


## ────────────── Получение текущей должности сотрудника ──────────────
async def get_current_position(employee_id: str, as_of_date: date = None) -> str:
    """
    Получает должность сотрудника на указанную дату
    
    Args:
        employee_id: ID сотрудника из iiko
        as_of_date: Дата, на которую нужна должность (по умолчанию - сегодня)
    
    Returns:
        Название должности или None
    """
    if as_of_date is None:
        as_of_date = date.today()
    
    async with async_session() as session:
        result = await session.execute(
            select(EmployeePositionHistory)
            .where(
                and_(
                    EmployeePositionHistory.employee_id == employee_id,
                    EmployeePositionHistory.valid_from <= as_of_date,
                    or_(
                        EmployeePositionHistory.valid_to >= as_of_date,
                        EmployeePositionHistory.valid_to.is_(None)
                    )
                )
            )
            .order_by(EmployeePositionHistory.valid_from.desc())
        )
        record = result.scalar_one_or_none()
        return record.position_name if record else None


## ────────────── Получение истории должностей за период ──────────────
async def get_position_history_for_period(employee_id: str, from_date: date, to_date: date) -> list:
    """
    Получает все периоды должностей сотрудника за указанный период
    
    Args:
        employee_id: ID сотрудника из iiko
        from_date: Начало периода
        to_date: Конец периода
    
    Returns:
        Список записей: [(position_name, period_start, period_end), ...]
    """
    async with async_session() as session:
        result = await session.execute(
            select(EmployeePositionHistory)
            .where(
                and_(
                    EmployeePositionHistory.employee_id == employee_id,
                    # Запись пересекается с запрашиваемым периодом
                    EmployeePositionHistory.valid_from <= to_date,
                    or_(
                        EmployeePositionHistory.valid_to >= from_date,
                        EmployeePositionHistory.valid_to.is_(None)
                    )
                )
            )
            .order_by(EmployeePositionHistory.valid_from)
        )
        records = result.scalars().all()
        
        periods = []
        for record in records:
            # Обрезаем период по границам запроса
            period_start = max(record.valid_from, from_date)
            period_end = min(record.valid_to or to_date, to_date)
            
            periods.append({
                'position_name': record.position_name,
                'valid_from': period_start,
                'valid_to': period_end
            })
        
        return periods


async def get_position_history_for_multiple_employees(employee_ids: list, from_date: date, to_date: date) -> dict:
    """
    Получает историю должностей для нескольких сотрудников одним запросом (оптимизация)
    
    Args:
        employee_ids: Список ID сотрудников из iiko
        from_date: Начало периода
        to_date: Конец периода
    
    Returns:
        Словарь {employee_id: [список периодов]}
    """
    if not employee_ids:
        return {}
    
    async with async_session() as session:
        result = await session.execute(
            select(EmployeePositionHistory)
            .where(
                and_(
                    EmployeePositionHistory.employee_id.in_(employee_ids),
                    # Запись пересекается с запрашиваемым периодом
                    EmployeePositionHistory.valid_from <= to_date,
                    or_(
                        EmployeePositionHistory.valid_to >= from_date,
                        EmployeePositionHistory.valid_to.is_(None)
                    )
                )
            )
            .order_by(EmployeePositionHistory.employee_id, EmployeePositionHistory.valid_from)
        )
        records = result.scalars().all()
        
        # Группируем по сотрудникам
        history_by_employee = {}
        for record in records:
            if record.employee_id not in history_by_employee:
                history_by_employee[record.employee_id] = []
            
            # Обрезаем период по границам запроса
            period_start = max(record.valid_from, from_date)
            period_end = min(record.valid_to or to_date, to_date)
            
            history_by_employee[record.employee_id].append({
                'position_name': record.position_name,
                'valid_from': period_start,
                'valid_to': period_end
            })
        
        return history_by_employee


## ────────────── Добавление/обновление должности ──────────────
async def set_employee_position(employee_id: str, employee_name: str, position_name: str, 
                                effective_date: date = None) -> None:
    """
    Устанавливает должность сотрудника с указанной даты
    Автоматически управляет периодами:
    - Закрывает текущий период
    - Удаляет полностью перекрываемые записи
    - Обрезает частично перекрываемые
    
    Args:
        employee_id: ID сотрудника из iiko
        employee_name: ФИО сотрудника
        position_name: Новая должность
        effective_date: С какой даты (по умолчанию - сегодня)
    """
    if effective_date is None:
        effective_date = date.today()
    
    async with async_session() as session:
        # 1. Получаем все существующие записи для этого сотрудника
        result = await session.execute(
            select(EmployeePositionHistory)
            .where(EmployeePositionHistory.employee_id == employee_id)
            .order_by(EmployeePositionHistory.valid_from)
        )
        existing_records = result.scalars().all()
        
        # 2. Обрабатываем существующие записи
        for record in existing_records:
            record_start = record.valid_from
            record_end = record.valid_to
            
            # Если запись полностью ДО новой даты - оставляем как есть
            if record_end and record_end < effective_date:
                continue
            
            # Если запись полностью ПОСЛЕ новой даты - удаляем
            if record_start >= effective_date:
                await session.delete(record)
                logger.debug(f"Удалена запись: {record.position_name} с {record_start}")
                continue
            
            # Если запись пересекается - обрезаем её до дня перед effective_date
            if record_start < effective_date:
                record.valid_to = effective_date - timedelta(days=1)
                logger.debug(f"Обрезана запись: {record.position_name} до {record.valid_to}")
        
        # 3. Создаем новую запись
        import uuid
        new_record = EmployeePositionHistory(
            id=str(uuid.uuid4()),
            employee_id=employee_id,
            employee_name=employee_name,
            position_name=position_name,
            valid_from=effective_date,
            valid_to=None  # Открытый период
        )
        session.add(new_record)
        
        await session.commit()
        logger.debug(f"Установлена должность для {employee_name}: {position_name} с {effective_date}")


## ────────────── Обновление должности из iiko (автомониторинг) ──────────────
async def update_position_from_iiko(employee_id: str, employee_name: str, 
                                    current_position: str, default_date: date = None) -> bool:
    """
    Обновляет должность сотрудника если она изменилась в iiko
    Используется автоматическим мониторингом
    
    Args:
        employee_id: ID сотрудника из iiko
        employee_name: ФИО сотрудника
        current_position: Текущая должность из iiko
        default_date: Дата для новых сотрудников (если None - используется сегодняшняя)
    
    Returns:
        True если была создана новая запись (должность изменилась или новый сотрудник)
    """
    stored_position = await get_current_position(employee_id)
    
    # Если должность не изменилась - ничего не делаем
    if stored_position == current_position:
        return False
    
    # Если это первая запись для сотрудника или должность изменилась
    today = date.today()
    
    if stored_position is None:
        # Первая запись - используем default_date если указана, иначе сегодняшнюю
        start_date = default_date if default_date else today
        logger.info(f"📝 Новый сотрудник {employee_name}: {current_position} (с {start_date.strftime('%d.%m.%Y')})")
        await set_employee_position(employee_id, employee_name, current_position, start_date)
    else:
        # Должность изменилась - используем сегодняшнюю дату
        logger.info(f"🔄 Изменение должности {employee_name}: {stored_position} → {current_position}")
        await set_employee_position(employee_id, employee_name, current_position, today)
    
    return True


## ────────────── Получение всех активных сотрудников ──────────────
async def get_all_active_employees() -> dict:
    """
    Получает всех сотрудников с их текущими должностями
    
    Returns:
        {employee_id: {'name': str, 'position': str, 'since': date}, ...}
    """
    today = date.today()
    
    async with async_session() as session:
        result = await session.execute(
            select(EmployeePositionHistory)
            .where(
                or_(
                    EmployeePositionHistory.valid_to >= today,
                    EmployeePositionHistory.valid_to.is_(None)
                )
            )
        )
        records = result.scalars().all()
        
        employees = {}
        for record in records:
            employees[record.employee_id] = {
                'name': record.employee_name,
                'position': record.position_name,
                'since': record.valid_from
            }
        
        return employees
