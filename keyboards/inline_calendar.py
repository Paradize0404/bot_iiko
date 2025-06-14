import calendar
from datetime import datetime
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from datetime import datetime

def build_calendar(year: int, month: int, calendar_id: str = "default", mode: str = "single") -> InlineKeyboardMarkup:
    """
    Собирает календарь с возможностью выбрать дату или период.
    :param year: Год
    :param month: Месяц
    :param calendar_id: Идентификатор календаря (для уникальности)
    :param mode: 'single' или 'range'
    :return: InlineKeyboardMarkup
    """
    keyboard = []

    # Заголовок
    keyboard.append([
        InlineKeyboardButton(
            text=f"{calendar.month_name[month]} {year}",
            callback_data=f"CAL:{calendar_id}:IGNORE"
        )
    ])

    # Дни недели
    days = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    keyboard.append([InlineKeyboardButton(text=day, callback_data=f"CAL:{calendar_id}:IGNORE") for day in days])

    # Дни месяца
    today = datetime.today().date()


    month_calendar = calendar.monthcalendar(year, month)
    for week in month_calendar:
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data=f"CAL:{calendar_id}:IGNORE"))
            else:
                date_str = f"{year}-{month:02d}-{day:02d}"
                row.append(InlineKeyboardButton(
                    text = f"🔷{day}" if day == today.day and year == today.year and month == today.month else str(day),
                    callback_data=f"CAL:{calendar_id}:DATE:{date_str}:{mode}"
                ))
        keyboard.append(row)

    # Кнопки навигации
    keyboard.append([
        InlineKeyboardButton(text="<<", callback_data=f"CAL:{calendar_id}:PREV:{year}:{month}:{mode}"),
        InlineKeyboardButton(text=">>", callback_data=f"CAL:{calendar_id}:NEXT:{year}:{month}:{mode}")
    ])

    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def parse_callback_data(callback_data: str):
    # Пример: CAL:salary:DATE:2025-06-11:range
    parts = callback_data.split(":")
    if len(parts) < 3 or parts[0] != "CAL":
        return None

    calendar_id = parts[1]
    action = parts[2]

    if action == "IGNORE":
        return {"action": "IGNORE", "calendar_id": calendar_id}

    if action in ["PREV", "NEXT"]:
        year, month, mode = int(parts[3]), int(parts[4]), parts[5]
        return {"action": action, "calendar_id": calendar_id, "year": year, "month": month, "mode": mode}

    if action == "DATE":
        date = datetime.strptime(parts[3], "%Y-%m-%d").date()
        mode = parts[4]
        return {"action": "DATE", "calendar_id": calendar_id, "date": date, "mode": mode}

    return None