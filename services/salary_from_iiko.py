"""
Получение данных по зарплатам напрямую из iiko API
Использует процент комиссии по должностям из БД для расчета бонусов
Учитывает историю изменений должностей сотрудников
"""
import httpx
import xml.etree.ElementTree as ET
from datetime import datetime, date as date_type
import logging
from iiko.iiko_auth import get_auth_token, get_base_url
from services.cash_shift_report import get_cash_shifts_with_details
from sqlalchemy import select
from db.position_commission_db import async_session, PositionCommission
from services.writeoff_documents import get_writeoff_documents, calculate_writeoff_sum_for_employee
from db.employee_position_history_db import get_position_history_for_period, update_position_from_iiko

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


## ────────────── Расчет выручки сотрудника по заказам ──────────────
def calculate_employee_revenue_by_orders(employee_attendances, cash_shifts, debug_name=None) -> float:
    """
    Рассчитывает выручку сотрудника на основе заказов, закрытых во время его работы
    Использует точное время закрытия каждого заказа из preset-отчета
    """
    emp_revenue = 0
    
    for shift in cash_shifts:
        try:
            s_start = _strip_tz(datetime.fromisoformat(normalize_isoformat(shift.get("openDate"))))
            s_end = _strip_tz(datetime.fromisoformat(normalize_isoformat(shift.get("closeDate"))))
            shift_orders = shift.get("orders", [])
            
            if debug_name:
                logger.info(
                    f"      🔍 Смена {s_start.strftime('%d.%m %H:%M')}-{s_end.strftime('%H:%M')}: "
                    f"{len(shift_orders)} заказов"
                )
                # Показываем attendance периоды сотрудника для этой смены
                matching_periods = [(a_s, a_e) for a_s, a_e in employee_attendances 
                                   if not (a_e < s_start or a_s > s_end)]
                if matching_periods:
                    logger.info(f"         📅 Attendance в эту смену:")
                    for a_s, a_e in matching_periods:
                        logger.info(f"            {a_s.strftime('%H:%M')}-{a_e.strftime('%H:%M')}")
                
                # Показываем первые 2 заказа для понимания формата
                if shift_orders:
                    logger.info(f"         📦 Примеры заказов:")
                    for i, order in enumerate(shift_orders[:2], 1):
                        logger.info(f"            {i}. {order.get('closeTime')}: {order.get('sum')}₽")
            
            if not shift_orders:
                if debug_name:
                    logger.info(f"         ⏭️ Нет заказов в эту смену")
                continue
            
            # Считаем выручку только от заказов, закрытых во время работы сотрудника
            shift_revenue = 0
            matched_orders = 0
            total_shift_revenue = sum(o.get('sum', 0) for o in shift_orders)
            
            if debug_name:
                logger.info(f"         📊 Общая выручка смены: {total_shift_revenue:.2f}₽")
            
            for order in shift_orders:
                try:
                    # Парсим время закрытия заказа
                    order_time_str = order.get('closeTime')
                    if not order_time_str:
                        continue
                    
                    # Парсим ISO формат или другие форматы
                    order_time = None
                    if 'T' in order_time_str:
                        # ISO формат: 2025-11-01T07:39:58.455
                        try:
                            order_time = datetime.fromisoformat(order_time_str.replace('Z', '+00:00'))
                        except ValueError:
                            pass
                    
                    if not order_time:
                        # Пробуем другие форматы
                        for fmt in ['%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%d.%m.%Y %H:%M:%S']:
                            try:
                                order_time = datetime.strptime(order_time_str, fmt)
                                break
                            except ValueError:
                                continue
                    
                    if not order_time:
                        if debug_name:
                            logger.debug(f"Не удалось распарсить время заказа: {order_time_str}")
                        continue
                    
                    # Убираем timezone для сравнения
                    order_time = _strip_tz(order_time)
                    
                    # Проверяем, был ли сотрудник на работе в момент закрытия заказа
                    for a_start, a_end in employee_attendances:
                        if a_start <= order_time <= a_end:
                            order_sum = order.get('sum', 0)
                            shift_revenue += order_sum
                            matched_orders += 1
                            if debug_name and matched_orders <= 5:  # Показываем первые 5 заказов для отладки
                                logger.info(f"            ✅ {order_time.strftime('%d.%m %H:%M:%S')}: {order_sum:.2f}₽")
                            break
                    
                except Exception as e:
                    logger.debug(f"Ошибка обработки заказа: {e}")
                    continue
            
            emp_revenue += shift_revenue
            
            if debug_name:
                if shift_revenue > 0:
                    logger.info(f"         ✅ Выручка сотрудника: {shift_revenue:.2f}₽ ({matched_orders} из {len(shift_orders)} заказов)")
                else:
                    logger.info(f"         ⏭️ Не работал в эту смену или нет подходящих заказов")
                
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
        
        # 4. Загружаем настройки комиссии из БД по должностям
        logger.info("📥 Загрузка настроек комиссии из БД...")
        position_settings = {}
        async with async_session() as session:
            result = await session.execute(select(PositionCommission))
            commissions = result.scalars().all()
            # Сохраняем все настройки: payment_type, fixed_rate, commission_percent, commission_type
            position_settings = {
                c.position_name: {
                    'payment_type': c.payment_type,
                    'fixed_rate': c.fixed_rate,
                    'commission_percent': c.commission_percent,
                    'commission_type': c.commission_type
                } 
                for c in commissions
            }
        
        logger.info(f"✅ Загружено {len(position_settings)} настроек по должностям")
        
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
            
            # Берем настройки из БД по полному названию должности
            settings = position_settings.get(position, {})
            
            employees_info[emp_id] = {
                'name': emp_name,
                'position': position,
                'deleted': emp.findtext("deleted", "false") == "true",
                'payment_type': settings.get('payment_type', 'hourly'),
                'fixed_rate': settings.get('fixed_rate'),
                'commission_percent': settings.get('commission_percent', 0.0),
                'commission_type': settings.get('commission_type', 'sales')
            }
        
        logger.info(f"✅ Загружено {len(employees_info)} сотрудников")
        
        # 6. Получаем кассовые смены с выручкой (для комиссии от продаж)
        logger.info("📥 Получение кассовых смен...")
        try:
            cash_shifts = await get_cash_shifts_with_details(from_date, to_date)
            logger.info(f"✅ Загружено {len(cash_shifts)} кассовых смен")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось получить кассовые смены: {e}")
            cash_shifts = []
        
        # 7. Получаем расходные накладные (для комиссии от расходных)
        logger.info("📥 Получение расходных накладных...")
        try:
            writeoff_docs = await get_writeoff_documents(from_date, to_date)
            logger.info(f"✅ Загружено {len(writeoff_docs)} расходных накладных")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось получить расходные накладные: {e}")
            writeoff_docs = []
        
        # 8. Обрабатываем attendance данные с учетом истории должностей
        salary_data = {}
        attendance_by_employee = {}  # Для расчета выручки/расходных
        attendance_with_dates = {}  # Храним attendance с датами для разделения по периодам
        
        # Преобразуем строки дат в date объекты для работы с историей
        period_start = datetime.strptime(from_date, "%Y-%m-%d").date()
        period_end = datetime.strptime(to_date, "%Y-%m-%d").date()
        
        for att in attendances:
            emp_id = att.findtext("employeeId")
            if not emp_id or emp_id not in employees_info:
                continue
            
            # Пропускаем удаленных сотрудников
            if employees_info[emp_id].get('deleted'):
                continue
            
            emp_info = employees_info[emp_id]
            
            # Инициализируем структуры данных
            if emp_id not in attendance_by_employee:
                attendance_by_employee[emp_id] = []
                attendance_with_dates[emp_id] = []
            
            # Собираем временные интервалы attendance
            try:
                date_from = att.findtext("dateFrom")
                date_to = att.findtext("dateTo")
                if date_from and date_to:
                    start = _strip_tz(datetime.fromisoformat(normalize_isoformat(date_from)))
                    end = _strip_tz(datetime.fromisoformat(normalize_isoformat(date_to)))
                    
                    # Извлекаем данные об оплате
                    regular_payment = 0
                    penalty = 0
                    payment_node = att.find("paymentDetails")
                    if payment_node is not None:
                        try:
                            regular_payment = float(payment_node.findtext("regularPaymentSum", "0"))
                            penalty = float(payment_node.findtext("penaltySum", "0"))
                        except Exception as e:
                            logger.warning(f"Ошибка парсинга paymentDetails для {emp_id}: {e}")
                    
                    # Сохраняем attendance с данными для последующей обработки по периодам
                    attendance_with_dates[emp_id].append({
                        'start': start,
                        'end': end,
                        'regular_payment': regular_payment,
                        'penalty': penalty
                    })
                    
                    # Также сохраняем в старый формат для расчета выручки
                    attendance_by_employee[emp_id].append((start, end))
                    
            except Exception as e:
                logger.warning(f"Ошибка обработки дат для {emp_id}: {e}")
        
        # 9. Получаем историю должностей и рассчитываем зарплаты по периодам
        logger.info("💰 Расчет зарплат с учетом истории должностей...")
        
        for emp_id in attendance_with_dates.keys():
            if emp_id not in employees_info:
                continue
            
            emp_info = employees_info[emp_id]
            emp_name = emp_info['name']
            
            # Получаем историю должностей за период расчета
            try:
                position_history = await get_position_history_for_period(emp_id, period_start, period_end)
            except Exception as e:
                logger.warning(f"⚠️ Не удалось получить историю должностей для {emp_name}: {e}. Используем текущую должность.")
                position_history = []
            
            # Если истории нет, используем текущую должность из iiko
            if not position_history:
                position_history = [{
                    'position_name': emp_info['position'],
                    'valid_from': period_start,
                    'valid_to': period_end
                }]
            
            # Обрабатываем каждый период должности отдельно
            for period in position_history:
                position_name = period['position_name']
                valid_from = period['valid_from']
                valid_to = period['valid_to'] or period_end  # NULL = до конца периода
                
                # Получаем настройки для этой должности
                settings = position_settings.get(position_name, {})
                payment_type = settings.get('payment_type', 'hourly')
                fixed_rate = settings.get('fixed_rate')
                commission_percent = settings.get('commission_percent', 0.0)
                commission_type = settings.get('commission_type', 'sales')
                
                logger.debug(f"  📋 {emp_name}: {position_name} ({valid_from} - {valid_to}), {payment_type}, комиссия {commission_percent}%")
                
                # Фильтруем attendance для этого периода должности
                period_attendances = []
                period_hours = 0
                period_work_days = 0
                period_regular_payment = 0
                period_penalty = 0
                
                for att_data in attendance_with_dates[emp_id]:
                    att_start = att_data['start']
                    att_end = att_data['end']
                    
                    # Проверяем, попадает ли attendance в период должности
                    att_date = att_start.date()
                    if valid_from <= att_date <= valid_to:
                        period_attendances.append((att_start, att_end))
                        
                        # Считаем часы
                        hours = (att_end - att_start).total_seconds() / 3600
                        period_hours += hours
                        period_work_days += 1
                        
                        # Базовая оплата и штрафы
                        if payment_type == 'hourly':
                            period_regular_payment += att_data['regular_payment']
                        
                        period_penalty += att_data['penalty']
                
                # Пропускаем период если нет работы
                if period_work_days == 0:
                    continue
                
                # Пересчитываем базовую оплату для посменной/помесячной
                if payment_type == 'per_shift' and fixed_rate:
                    period_regular_payment = fixed_rate * period_work_days
                    logger.debug(f"    💵 Посменная: {fixed_rate}₽ × {period_work_days} смен = {period_regular_payment}₽")
                
                elif payment_type == 'monthly' and fixed_rate:
                    # Для помесячной выплачиваем пропорционально дням работы
                    # Можно сделать полную выплату, если работал хотя бы 1 день в периоде
                    period_regular_payment = fixed_rate
                    logger.debug(f"    💵 Помесячная: {fixed_rate}₽")
                
                # Рассчитываем комиссию для этого периода
                period_bonus = 0
                period_revenue = 0
                
                if commission_percent > 0 and period_attendances:
                    if commission_type == 'sales' and cash_shifts:
                        # Комиссия от продаж
                        revenue = calculate_employee_revenue_by_orders(
                            period_attendances,
                            cash_shifts,
                            debug_name=None
                        )
                        period_revenue = revenue
                        
                        if revenue > 0:
                            period_bonus = round(revenue * (commission_percent / 100), 2)
                            logger.debug(f"    💰 Выручка: {revenue:.2f}₽ × {commission_percent}% = {period_bonus:.2f}₽")
                    
                    elif commission_type == 'writeoff' and writeoff_docs:
                        # Комиссия от расходных накладных
                        writeoff_sum, filtered_docs = calculate_writeoff_sum_for_employee(
                            writeoff_docs,
                            period_attendances
                        )
                        period_revenue = writeoff_sum
                        
                        if writeoff_sum > 0:
                            period_bonus = round(writeoff_sum * (commission_percent / 100), 2)
                            logger.debug(f"    💰 Расходные накладные: {writeoff_sum:.2f}₽ × {commission_percent}% = {period_bonus:.2f}₽ ({len(filtered_docs)} накл.)")
                
                # Создаем уникальный ключ для каждого периода: emp_id + должность + период
                period_key = f"{emp_id}_{position_name}_{valid_from}"
                
                # Создаем отдельную запись для этого периода должности
                salary_data[period_key] = {
                    'name': emp_name,
                    'position': position_name,  # Должность в этом периоде
                    'payment_type': payment_type,
                    'fixed_rate': fixed_rate,
                    'total_hours': period_hours,
                    'work_days': period_work_days,
                    'regular_payment': period_regular_payment,
                    'bonus': period_bonus,
                    'penalty': period_penalty,
                    'total_payment': period_regular_payment + period_bonus - period_penalty,
                    'revenue': period_revenue,
                    'commission_percent': commission_percent,
                    'commission_type': commission_type,
                    'period_start': valid_from,  # Добавляем информацию о периоде для отображения
                    'period_end': valid_to
                }
                
                logger.info(f"✅ {emp_name} ({position_name}, {valid_from} - {valid_to}): {salary_data[period_key]['total_payment']:.2f}₽")
        
        logger.info(f"✅ Загружены данные по {len(salary_data)} записям (сотрудники × периоды)")
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
            # Проверяем, есть ли информация о периоде (для сотрудников с несколькими должностями)
            period_info = ""
            if 'period_start' in emp and 'period_end' in emp:
                period_start = emp['period_start']
                period_end = emp['period_end']
                # Показываем период только если он не охватывает весь расчетный период
                if period_start.strftime("%Y-%m-%d") != from_date or period_end.strftime("%Y-%m-%d") != to_date:
                    period_info = f" (📅 {period_start.strftime('%d.%m')} - {period_end.strftime('%d.%m')})"
            
            # Информация о типе оплаты
            payment_type = emp.get('payment_type', 'hourly')
            if payment_type == 'hourly':
                payment_info = f"⏱️ Часы: {emp['total_hours']:.1f} ч ({emp['work_days']} дн.)"
            elif payment_type == 'per_shift':
                fixed_rate = emp.get('fixed_rate', 0)
                payment_info = f"📅 Смены: {emp['work_days']} × {fixed_rate:.0f}₽"
            else:  # monthly
                fixed_rate = emp.get('fixed_rate', 0)
                payment_info = f"📆 Месяц: {fixed_rate:.0f}₽"
            
            lines.append(
                f"  • {emp['name']}{period_info}\n"
                f"    {payment_info}\n"
                f"    💵 Оплата: {emp['regular_payment']:.2f} ₽"
            )
            
            # Показываем комиссию только если она есть
            commission_type = emp.get('commission_type', 'sales')
            commission_percent = emp.get('commission_percent', 0)
            
            if emp['bonus'] > 0:
                if commission_type == 'sales':
                    commission_label = "💰 от продаж"
                else:  # writeoff
                    commission_label = "📦 от расходных накладных"
                
                lines.append(
                    f"    📈 Комиссия ({commission_percent:.1f}% {commission_label}): "
                    f"+{emp['bonus']:.2f} ₽ (база: {emp['revenue']:.2f} ₽)"
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
