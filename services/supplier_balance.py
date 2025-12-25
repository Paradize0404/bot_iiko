"""
Баланс по поставщикам (конечный остаток по счёту «Задолженность перед поставщиками»)
"""
import httpx
import xml.etree.ElementTree as ET
from decimal import Decimal
from datetime import datetime
import logging

from iiko.iiko_auth import get_auth_token, get_base_url

logger = logging.getLogger(__name__)


def _auto_cast(text):
    if text is None:
        return None
    try:
        return int(text)
    except Exception:
        try:
            return Decimal(text)
        except Exception:
            return text.strip() if text else None


def parse_xml_report(xml: str):
    root = ET.fromstring(xml)
    return [{child.tag: _auto_cast(child.text) for child in row} for row in root.findall("./r")]


async def get_supplier_balance(
    date_str: str | None = None,
    blacklist_markers: list[str] | None = None,
    min_amount: Decimal = Decimal("0"),
) -> dict:
    """Возвращает положительный долг перед поставщиками на выбранную дату."""
    blacklist_markers = blacklist_markers or ["Рынок", "Кофейня"]

    try:
        token = await get_auth_token()
        base_url = get_base_url()

        if not date_str:
            date_str = datetime.now().strftime("%d.%m.%Y")
        elif "-" in date_str:
            date_str = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m.%Y")

        params = [
            ("key", token),
            ("report", "TRANSACTIONS"),
            ("from", "01.01.2020"),
            ("to", date_str),
            ("groupRow", "Account.Name"),
            ("groupRow", "Counteragent.Name"),
            ("agr", "FinalBalance.Money"),
            ("Account.CounteragentType", "SUPPLIER"),
            ("Counteragent", "SUPPLIER"),
            ("Account.Group", "LIABILITIES"),
        ]

        async with httpx.AsyncClient(base_url=base_url, timeout=120, verify=False) as client:
            r = await client.get("/resto/api/reports/olap", params=params)
            if r.status_code != 200:
                logger.error("❌ Ошибка получения баланса по поставщикам %s: %s", date_str, r.text[:300])
                return {"date": date_str, "suppliers": [], "total": Decimal(0)}

            ct = r.headers.get("content-type", "")
            if ct.startswith("application/json"):
                data = r.json()
                rows = data.get("data", []) or data.get("rows", [])
            elif ct.startswith("application/xml") or ct.startswith("text/xml"):
                rows = parse_xml_report(r.text)
            else:
                logger.error("⚠️ Неизвестный формат ответа: %s", ct)
                return {"date": date_str, "suppliers": [], "total": Decimal(0)}

        # Оставляем только строки по счёту "Задолженность перед поставщиками"
        debt_rows = [row for row in rows if str(row.get("Account.Name")) == "Задолженность перед поставщиками"]

        agg: dict[str, Decimal] = {}
        for row in debt_rows:
            name = str(row.get("Counteragent.Name") or "N/A")
            if any(marker in name for marker in blacklist_markers):
                continue
            val_raw = row.get("FinalBalance.Money", 0) or 0
            try:
                val = Decimal(str(val_raw).replace(",", "."))
            except Exception:
                val = Decimal(0)
            if val <= 0 or val < min_amount:
                continue
            agg[name] = agg.get(name, Decimal(0)) + val

        suppliers = [
            {"name": name, "balance": bal}
            for name, bal in sorted(agg.items(), key=lambda x: x[1], reverse=True)
        ]
        total = sum((s["balance"] for s in suppliers), Decimal(0))

        return {"date": date_str, "suppliers": suppliers, "total": total}

    except Exception as e:
        logger.exception("❌ Ошибка получения баланса по поставщикам: %s", e)
        return {"date": date_str or datetime.now().strftime("%d.%m.%Y"), "suppliers": [], "total": Decimal(0)}


def format_supplier_balance_report(balance_data: dict, limit: int | None = None) -> str:
    lines: list[str] = []
    lines.append(f"📊 Баланс по поставщикам на {balance_data['date']}")
    lines.append("=" * 60)

    suppliers = balance_data.get("suppliers", [])
    if not suppliers:
        lines.append("Нет положительных долгов по счёту 'Задолженность перед поставщиками'.")
        return "\n".join(lines)

    slice_suppliers = suppliers if limit is None else suppliers[:limit]

    for idx, supplier in enumerate(slice_suppliers, 1):
        lines.append(f"{idx}. {supplier['name']} — {supplier['balance']:,.2f}₽")

    if limit is not None and len(suppliers) > limit:
        lines.append(f"… и ещё {len(suppliers) - limit} поставщиков")

    lines.append("" )
    lines.append(f"ИТОГО: {balance_data.get('total', Decimal(0)):,.2f}₽")
    return "\n".join(lines)
