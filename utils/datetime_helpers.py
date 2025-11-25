"""
Общие утилиты для работы с датами и временем
Выделены из разных модулей для избежания дублирования кода
"""
from datetime import datetime


def strip_tz(dt: datetime) -> datetime:
    """
    Убирает timezone из datetime для корректного сравнения дат
    
    Args:
        dt: datetime объект с возможным timezone
        
    Returns:
        datetime без timezone
    """
    return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt


def normalize_isoformat(dt_str: str) -> str:
    """
    Нормализует ISO формат даты для корректного парсинга
    Исправляет миллисекунды до 6 знаков (Python требует ровно 6)
    
    Args:
        dt_str: строка с датой в ISO формате
        
    Returns:
        нормализованная строка даты
    """
    if not dt_str:
        return dt_str
    
    if '.' in dt_str:
        date_part, ms = dt_str.split('.', 1)
        tz = ''
        for sym in ['+', '-']:
            if sym in ms:
                ms, tz = ms.split(sym, 1)
                tz = sym + tz
                break
        ms_digits = ''.join(filter(str.isdigit, ms))
        ms_fixed = (ms_digits + '000000')[:6]
        return f"{date_part}.{ms_fixed}{tz}"
    
    return dt_str


def parse_datetime(dt_str: str) -> datetime | None:
    """
    Универсальный парсер дат из различных форматов
    
    Args:
        dt_str: строка с датой
        
    Returns:
        datetime объект или None при ошибке
    """
    if not dt_str:
        return None
    
    # ISO формат с T
    if 'T' in dt_str:
        try:
            return datetime.fromisoformat(
                normalize_isoformat(dt_str.replace('Z', '+00:00'))
            )
        except ValueError:
            pass
    
    # Другие форматы
    formats = [
        '%Y-%m-%d %H:%M:%S.%f',
        '%Y-%m-%d %H:%M:%S',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d',
        '%d.%m.%Y %H:%M:%S',
        '%d.%m.%Y %H:%M',
        '%d.%m.%Y',
    ]
    
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    
    return None


def parse_datetime_safe(dt_str: str) -> datetime:
    """
    Парсит дату с нормализацией ISO формата
    Выбрасывает исключение при ошибке
    
    Args:
        dt_str: строка с датой в ISO формате
        
    Returns:
        datetime объект
    """
    return strip_tz(datetime.fromisoformat(normalize_isoformat(dt_str)))
