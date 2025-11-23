"""
Получение данных по зарплатам напрямую из iiko API
Использует процент комиссии по должностям из БД для расчета бонусов
"""
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime
import logging
from iiko.iiko_auth import get_auth_token, get_base_url
from services.cash_shift_report import get_cash_shifts_with_details
from sqlalchemy import select
from db.position_commission_db import async_session, PositionCommission

logger = logging.getLogger(__name__)
# Временно повышаем уровень для отладки
logger.setLevel(logging.DEBUG)


## ────────────── Вспомогательные функции ──────────────
def _strip_tz(dt):
    """Убирает timezone из datetime"""
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def normalize_isoformat(dt_str: str) -> str:
    """Нормализует ISO формат даты"""
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


## ────────────── Расчет выручки сотрудника ──────────────
def calculate_employee_revenue(employee_attendances, cash_shifts, debug_name=None) -> float:
    """
    Рассчитывает выручку сотрудника ПРОПОРЦИОНАЛЬНО отработанным часам в каждой смене
    Формула: (часы_работы_в_смене / длительность_смены) × выручка_смены
    Это справедливо: если работал половину смены - получает половину выручки
    """
    emp_revenue = 0
    
    for shift in cash_shifts:
        try:
            s_start = _strip_tz(datetime.fromisoformat(normalize_isoformat(shift.get("openDate"))))
            s_end = _strip_tz(datetime.fromisoformat(normalize_isoformat(shift.get("closeDate"))))
            shift_duration = (s_end - s_start).total_seconds() / 3600
            
            if shift_duration <= 0:
                continue
            
            shift_revenue = shift.get("payOrders", 0)
            
            if debug_name:
                logger.info(
                    f"      🔍 Смена {s_start.strftime('%d.%m %H:%M')}-{s_end.strftime('%H:%M')}: "
                    f"выручка {shift_revenue:.2f}₽, длительность {shift_duration:.2f}ч"
                )
            
            # Ищем пересечение рабочего времени сотрудника со сменой
            shift_employee_hours = 0
            
            for a_start, a_end in employee_attendances:
                overlap_start = max(a_start, s_start)
                overlap_end = min(a_end, s_end)
                
                if overlap_start < overlap_end:
                    # Часы работы сотрудника в эту смену
                    overlap_hours = (overlap_end - overlap_start).total_seconds() / 3600
                    shift_employee_hours += overlap_hours
            
            if shift_employee_hours > 0:
                # Пропорция: отработанные_часы / длительность_смены
                proportion = shift_employee_hours / shift_duration
                revenue_for_shift = shift_revenue * proportion
                emp_revenue += revenue_for_shift
                
                if debug_name:
                    logger.info(
                        f"         ✅ Работал {shift_employee_hours:.2f}ч из {shift_duration:.2f}ч "
                        f"({proportion:.1%}) → +{revenue_for_shift:.2f}₽"
                    )
            elif debug_name:
                logger.info(f"         ⏭️ Не работал в эту смену")
                
        except Exception as e:
            logger.warning(f"Ошибка при расчете выручки для смены: {e}")
            continue
    
    return round(emp_revenue, 2)


## ────────────── Получение данных о зарплате из iiko API ──────────────
async def fetch_salary_from_iiko(from_date: str, to_date: str) -> dict:
    """
    Получает полные данные о зарплате сотрудников из iiko API
    Использует attendance для часов и оплаты, cash_shifts для выручки,
    процент комиссии берется из БД по должностям
    
    Returns:
        dict: {
            employee_id: {
                'name': str,
                'position': str,
                'total_hours': float,
                'work_days': int,
                'regular_payment': float,
                'bonus': float,             # Бонусы от мотивационной программы
                'penalty': float,
                'total_payment': float,
                'revenue': float,           # Выручка за смены
                'bonus_percent': float      # Процент от выручки
            }
        }
    """
    try:
        token = await get_auth_token()
        base_url = get_base_url()
        
        # 1. Получаем attendance с деталями оплаты
        logger.info("📥 Получение attendance...")
        attendance_url = f"{base_url}/resto/api/employees/attendance/"
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            response = await client.get(
                attendance_url,
                headers={"Cookie": f"key={token}"},
                params={
                    "from": from_date,
                    "to": to_date,
                    "withPaymentDetails": "true"
                }
            )
        response.raise_for_status()
        
        # Парсим XML
        tree = ET.fromstring(response.text)
        attendances = tree.findall(".//attendance")
        logger.info(f"✅ Получено {len(attendances)} записей attendance")
        
        # 2. Получаем информацию о сотрудниках
        logger.info("📥 Получение списка сотрудников из iiko...")
        employees_url = f"{base_url}/resto/api/employees"
        
        # Пробуем с разными параметрами
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            # Сначала пробуем с includeDeleted
            emp_response = await client.get(
                employees_url,
                headers={"Cookie": f"key={token}"},
                params={"includeDeleted": "false"}
            )
            
            if emp_response.status_code != 200:
                # Если не сработало, пробуем без параметров
                emp_response = await client.get(
                    employees_url,
                    headers={"Cookie": f"key={token}"}
                )
        
        emp_response.raise_for_status()
        emp_tree = ET.fromstring(emp_response.text)
        
        # 3. Получаем справочник должностей (код → название)
        logger.info("📥 Получение справочника должностей...")
        roles_url = f"{base_url}/resto/api/employees/roles"
        async with httpx.AsyncClient(verify=False, timeout=30.0) as client:
            roles_response = await client.get(
                roles_url,
                headers={"Cookie": f"key={token}"}
            )
        
        roles_response.raise_for_status()
        roles_tree = ET.fromstring(roles_response.text)
        
        # Создаем словарь {код: полное_название}
        roles_dict = {}
        for role in roles_tree.findall('.//role'):
            code = role.findtext('code')
            name = role.findtext('name')
            if code and name:
                roles_dict[code] = name
        
        logger.info(f"✅ Загружено {len(roles_dict)} должностей")
        
        # 4. Загружаем проценты комиссии из БД по должностям
        logger.info("📥 Загрузка процентов комиссии из БД...")
        position_commissions = {}
        async with async_session() as session:
            result = await session.execute(select(PositionCommission))
            commissions = result.scalars().all()
            position_commissions = {c.position_name: c.commission_percent for c in commissions}
        
        logger.info(f"✅ Загружено {len(position_commissions)} процентов по должностям")
        
        # 5. Создаем справочник сотрудников
        employees_info = {}
        
        for emp in emp_tree.findall(".//employee"):
            emp_id = emp.findtext("id")
            emp_name = emp.findtext("name", "Неизвестно")
            if not emp_id:
                continue
            
            # Получаем код должности и преобразуем в полное название
            position_code = None
            role_codes_element = emp.find('roleCodes')
            if role_codes_element is not None:
                role_code = role_codes_element.find('string')
                if role_code is not None and role_code.text:
                    position_code = role_code.text
            
            # Также проверяем mainRoleCode как запасной вариант
            if not position_code:
                position_code = emp.findtext('mainRoleCode')
            
            # Преобразуем код в полное название
            position = roles_dict.get(position_code, "—") if position_code else "—"
            
            # Debug: логируем первых 3 сотрудников
            if len(employees_info) < 3:
                logger.info(f"🔍 Сотрудник: {emp_name}, код: '{position_code}' → должность: '{position}'")
            
            # Берем процент из БД по полному названию должности
            bonus_percent = position_commissions.get(position, 0.0)
            
            employees_info[emp_id] = {
                'name': emp_name,
                'position': position,
                'deleted': emp.findtext("deleted", "false") == "true",
                'bonus_percent': bonus_percent
            }
        
        logger.info(f"✅ Загружено {len(employees_info)} сотрудников")
        
        # 6. Получаем кассовые смены с выручкой
        logger.info("📥 Получение кассовых смен...")
        try:
            cash_shifts = await get_cash_shifts_with_details(from_date, to_date)
            logger.info(f"✅ Загружено {len(cash_shifts)} кассовых смен")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось получить кассовые смены: {e}")
            cash_shifts = []
        
        # 7. Обрабатываем attendance данные
        salary_data = {}
        attendance_by_employee = {}  # Для расчета выручки
        
        for att in attendances:
            emp_id = att.findtext("employeeId")
            if not emp_id or emp_id not in employees_info:
                continue
            
            # Пропускаем удаленных сотрудников
            if employees_info[emp_id].get('deleted'):
                continue
            
            # Инициализируем запись если её нет
            if emp_id not in salary_data:
                salary_data[emp_id] = {
                    'name': employees_info[emp_id]['name'],
                    'position': employees_info[emp_id]['position'],
                    'total_hours': 0,
                    'work_days': 0,
                    'regular_payment': 0,
                    'bonus': 0,
                    'penalty': 0,
                    'total_payment': 0,
                    'revenue': 0,
                    'bonus_percent': employees_info[emp_id]['bonus_percent']
                }
                attendance_by_employee[emp_id] = []
            
            # Собираем временные интервалы attendance для расчета выручки
            try:
                date_from = att.findtext("dateFrom")
                date_to = att.findtext("dateTo")
                if date_from and date_to:
                    start = _strip_tz(datetime.fromisoformat(normalize_isoformat(date_from)))
                    end = _strip_tz(datetime.fromisoformat(normalize_isoformat(date_to)))
                    attendance_by_employee[emp_id].append((start, end))
                    
                    # Считаем часы
                    hours = (end - start).total_seconds() / 3600
                    salary_data[emp_id]['total_hours'] += hours
                    salary_data[emp_id]['work_days'] += 1
            except Exception as e:
                logger.warning(f"Ошибка обработки дат для {emp_id}: {e}")
            
            # Извлекаем данные об оплате из paymentDetails
            payment_node = att.find("paymentDetails")
            if payment_node is not None:
                try:
                    # Базовая оплата
                    regular = float(payment_node.findtext("regularPaymentSum", "0"))
                    salary_data[emp_id]['regular_payment'] += regular
                    
                    # Штрафы
                    penalty = float(payment_node.findtext("penaltySum", "0"))
                    salary_data[emp_id]['penalty'] += penalty
                    
                except Exception as e:
                    logger.warning(f"Ошибка парсинга paymentDetails для {emp_id}: {e}")
        
        # 8. Рассчитываем выручку и бонусы для каждого сотрудника
        logger.info("💰 Расчет бонусов от выручки...")
        for emp_id, data in salary_data.items():
            # Рассчитываем выручку за смены сотрудника
            if cash_shifts and emp_id in attendance_by_employee:
                # Детальное логирование для отладки
                if "Сорокина В" in data['name']:
                    logger.info(f"🔍 ДЕТАЛЬНЫЙ РАСЧЕТ ДЛЯ: {data['name']}")
                    logger.info(f"   Attendance периоды: {len(attendance_by_employee[emp_id])}")
                    for idx, (a_start, a_end) in enumerate(attendance_by_employee[emp_id], 1):
                        duration = (a_end - a_start).total_seconds() / 3600
                        logger.info(f"   {idx}. {a_start} - {a_end} ({duration:.1f}ч)")
                    logger.info(f"   Кассовых смен: {len(cash_shifts)}")
                    logger.info("   Расчет по сменам:")
                
                revenue = calculate_employee_revenue(
                    attendance_by_employee[emp_id],
                    cash_shifts,
                    debug_name=data['name'] if "Сорокина В" in data['name'] else None
                )
                data['revenue'] = revenue
                
                # Рассчитываем бонус
                if data['bonus_percent'] > 0 and revenue > 0:
                    bonus = round(revenue * (data['bonus_percent'] / 100), 2)
                    data['bonus'] = bonus
                    if "Сорокина В" in data['name']:
                        logger.info(
                            f"   ✅ ИТОГ: Выручка={revenue:.2f}₽, Процент={data['bonus_percent']}%, "
                            f"Бонус={bonus:.2f}₽"
                        )
            
            # Итоговая сумма
            data['total_payment'] = data['regular_payment'] + data['bonus'] - data['penalty']
        
        logger.info(f"✅ Загружены данные по {len(salary_data)} сотрудникам")
        return salary_data
        
    except Exception as e:
        logger.exception(f"❌ Ошибка получения зарплат из iiko: {e}")
        return {}


## ────────────── Форматирование отчета ──────────────
def format_salary_report(salary_data: dict, from_date: str, to_date: str) -> str:
    """
    Форматирует данные зарплаты в читаемый отчет
    """
    if not salary_data:
        return "⚠️ Нет данных по зарплатам за указанный период"
    
    lines = [
        f"💰 <b>Отчет по зарплатам</b>",
        f"📅 Период: {from_date} — {to_date}\n"
    ]
    
    # Группируем по должностям
    by_position = {}
    for emp_id, data in salary_data.items():
        pos = data['position']
        if pos not in by_position:
            by_position[pos] = []
        by_position[pos].append(data)
    
    total_sum = 0
    
    for position, employees in sorted(by_position.items()):
        lines.append(f"\n👥 <b>{position}</b>")
        position_total = 0
        
        for emp in sorted(employees, key=lambda x: x['name']):
            lines.append(
                f"  • {emp['name']}\n"
                f"    ⏱ Часы: {emp['total_hours']:.1f} ч ({emp['work_days']} дн.)\n"
                f"    💵 Оплата: {emp['regular_payment']:.2f} ₽"
            )
            
            # Показываем бонусы только если они есть
            if emp['bonus'] > 0:
                lines.append(
                    f"    📈 Бонусы ({emp['bonus_percent']:.1f}% от выручки): "
                    f"+{emp['bonus']:.2f} ₽ (выручка: {emp['revenue']:.2f} ₽)"
                )
            
            if emp['penalty'] > 0:
                lines.append(f"    ⚠️ Штрафы: -{emp['penalty']:.2f} ₽")
            
            lines.append(f"    ✅ <b>Итого: {emp['total_payment']:.2f} ₽</b>\n")
            
            position_total += emp['total_payment']
            total_sum += emp['total_payment']
        
        lines.append(f"  💼 <b>Итого по должности: {position_total:.2f} ₽</b>")
    
    lines.append(f"\n\n💰 <b>ИТОГО К ВЫПЛАТЕ: {total_sum:.2f} ₽</b>")
    
    return "\n".join(lines)


## ────────────── Основная функция получения отчета ──────────────
async def get_salary_report_from_iiko(from_date: str, to_date: str) -> str:
    """
    Получает и форматирует отчет по зарплатам из iiko API
    """
    salary_data = await fetch_salary_from_iiko(from_date, to_date)
    return format_salary_report(salary_data, from_date, to_date)
