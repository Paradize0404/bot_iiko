from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from db.employees_db import Employee
from services.cash_shift_report import get_cash_shifts_with_details
from iiko.iiko_auth import get_auth_token, get_base_url
import httpx
import xml.etree.ElementTree as ET
from calendar import monthrange
import logging


## ────────────── Логгер ──────────────
logger = logging.getLogger(__name__)

## ────────────── Вспомогательные функции для дат ──────────────
def normalize_isoformat(dt_str: str) -> str:
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

def _strip_tz(dt):
    return dt.replace(tzinfo=None) if dt.tzinfo else dt

## ────────────── Загрузка сотрудников из БД ──────────────
async def load_employees_from_db(session: AsyncSession):
    result = await session.execute(select(Employee))
    employees = result.scalars().all()
    return {
        emp.id: {
            "name": f"{emp.first_name} {emp.last_name}",
            "rate": emp.rate,
            "commission_percent": emp.commission_percent or 0,
            "monthly": emp.monthly,
            "per_shift": emp.per_shift,
            "department": emp.department or "не указан"
        }
        for emp in employees
    }

## ────────────── Получение данных посещаемости из iiko ──────────────
async def fetch_attendance_data(token: str, base_url: str, from_date: str, to_date: str):
    url = f"{base_url}/resto/api/employees/attendance/"
    async with httpx.AsyncClient(verify=False) as client:
        response = await client.get(url, headers={"Cookie": f"key={token}"}, params={
            "from": from_date,
            "to": to_date,
            "withPaymentDetails": "true"
        })
    response.raise_for_status()
    tree = ET.fromstring(response.text)
    return tree.findall(".//attendance")

## ────────────── Обработка данных посещаемости ──────────────
def process_attendance(attendances, employee_ids):
    total_by_employee = {}
    payments_by_employee = {}
    work_days_by_employee = {}

    for att in attendances:
        eid = att.findtext("employeeId")
        if eid not in employee_ids:
            logger.warning(f"🛑 employeeId {eid} не найден в базе")
            continue
        try:
            start = datetime.fromisoformat(normalize_isoformat(att.findtext("dateFrom")))
            end = datetime.fromisoformat(normalize_isoformat(att.findtext("dateTo")))
            hours = (end - start).total_seconds() / 3600
            total_by_employee[eid] = total_by_employee.get(eid, 0) + hours
            work_days_by_employee[eid] = work_days_by_employee.get(eid, 0) + 1

            payment_node = att.find("paymentDetails")
            if payment_node is not None:
                reg_sum = float(payment_node.findtext("regularPaymentSum", "0.0"))
                payments_by_employee[eid] = payments_by_employee.get(eid, 0) + reg_sum
        except Exception as e:
            logger.exception(f"⚠️ Ошибка в обработке attendance: {e}")
            continue

    return total_by_employee, payments_by_employee, work_days_by_employee

## ────────────── Формирование итогового отчёта ──────────────
def build_report(
    employee_data,
    total_by_employee,
    payments_by_employee,
    work_days_by_employee,
    from_date,
    to_date,
    personal_revenue_by_employee
) -> str:
    result = [f"\U0001F4CA <b>Отчёт за период {from_date} – {to_date}:</b>\n"]

    total_revenue_sum = sum(personal_revenue_by_employee.values())
    result.append(f"\n\U0001F4B0 Сумма всей выручки по сотрудникам: {total_revenue_sum:,.2f} ₽")

    total_sum = 0
    department_blocks = {}
    department_totals = {}
    start_dt = datetime.fromisoformat(normalize_isoformat(from_date))
    days_in_month = monthrange(start_dt.year, start_dt.month)[1]
    selected_days = (datetime.fromisoformat(normalize_isoformat(to_date)) - start_dt).days + 1

    for eid, info in employee_data.items():
        name = info["name"]
        rate = info.get("rate") or 0.0
        if info.get("rate") is None:
            logger.warning(f"⚠️ У сотрудника {info['name']} отсутствует ставка (rate)")
        percent = info.get("commission_percent", 0.0)
        monthly = info.get("monthly", False)
        per_shift = info.get("per_shift", False)
        department = info.get("department", "не указан")

        hours = total_by_employee.get(eid, 0.0)
        shifts = work_days_by_employee.get(eid, 0)

        if not monthly and not per_shift and hours == 0:
            continue
        if per_shift and shifts == 0:
            continue

        paid_sum = round(payments_by_employee.get(eid, 0.0), 2)
        commission_sum = 0

        personal_revenue = personal_revenue_by_employee.get(eid, 0)
        if percent > 0 and personal_revenue:
            commission_sum = round(personal_revenue * (percent / 100), 2)

        if monthly:
            work_days = selected_days
            daily_salary = round(rate / days_in_month, 2)
            total_pay = round(daily_salary * work_days, 2)
            paid_sum = total_pay
        elif per_shift:
            total_pay = round(rate * shifts, 2)
            paid_sum = total_pay
        else:
            total_pay = round(rate * hours, 2)

        final_paid = paid_sum + commission_sum
        total_sum += final_paid

        if department not in department_blocks:
            department_blocks[department] = []
            department_totals[department] = 0
        department_totals[department] += final_paid

        block = f"\U0001F464 <b>{name}</b>\n"
        if monthly:
            block += f"\U0001F4C5 Оклад: {rate:,.0f} ₽ ÷ {days_in_month} дн. × {work_days} дн. = {total_pay:,.2f} ₽\n"
        elif per_shift:
            block += f"\U0001F4CB Смен: {shifts} × {rate:,.0f} ₽ = {total_pay:,.2f} ₽\n"
        else:
            block += f"⏱ {hours:.2f} ч × {rate:.0f} ₽ = {total_pay:,.0f} ₽\n"

        block += f"\U0001F4B0 Начислено: {final_paid:,.2f} ₽"
        if percent > 0 and personal_revenue:
            block += f" ({paid_sum:,.2f} ₽ + {commission_sum:,.2f} ₽)"
        block += "\n"

        if percent > 0 and personal_revenue:
            block += f"\U0001F4C8 {percent:.1f}% от выручки за свои смены: {commission_sum:,.2f} ₽ (сумма смен: {personal_revenue:,.2f} ₽)\n"

        department_blocks[department].append(block)

    for department, blocks in department_blocks.items():
        result.append(f"\n<b>\U0001F4CC Отдел: {department}</b>")
        result.extend(blocks)
        result.append(f"<b>Итого по отделу: {department_totals[department]:,.2f} ₽</b>\n")

    result.append(f"\n\U0001F9FE <b>Общая сумма выплат:</b> {total_sum:,.2f} ₽")
    return "\n".join(result)

## ────────────── Основная функция получения отчёта по зарплате ──────────────
async def get_salary_report(from_date: str, to_date: str, db_session: AsyncSession) -> str:
    try:
        token = await get_auth_token()
        base_url = get_base_url()
        employee_data = await load_employees_from_db(db_session)
        employee_ids = set(employee_data.keys())
        attendances = await fetch_attendance_data(token, base_url, from_date, to_date)
        logger.info(f"✅ Загружены сотрудники: {employee_ids}")
        total_by_emp, payments_by_emp, work_days_by_emp = process_attendance(attendances, employee_ids)

        try:
            cash_shifts = await get_cash_shifts_with_details(from_date, to_date)
        except Exception as e:
            return f"❌ Ошибка при получении смен: {e}"

        personal_revenue_by_employee = {}
        for eid in employee_ids:
            emp_att = [
                (
                    _strip_tz(datetime.fromisoformat(normalize_isoformat(att.findtext("dateFrom")))),
                    _strip_tz(datetime.fromisoformat(normalize_isoformat(att.findtext("dateTo"))))
                )
                for att in attendances
                if att.findtext("employeeId") == eid
            ]
            emp_revenue = 0
            for shift in cash_shifts:
                s_start = _strip_tz(datetime.fromisoformat(normalize_isoformat(shift.get("openDate"))))
                s_end = _strip_tz(datetime.fromisoformat(normalize_isoformat(shift.get("closeDate"))))
                for a_start, a_end in emp_att:
                    if max(a_start, s_start) < min(a_end, s_end):
                        emp_revenue += shift.get("payOrders", 0)
                        break
            personal_revenue_by_employee[eid] = emp_revenue

        return build_report(
            employee_data,
            total_by_emp,
            payments_by_emp,
            work_days_by_emp,
            from_date,
            to_date,
            personal_revenue_by_employee
        )
    except Exception as e:
        return f"❌ Ошибка: {e}"
