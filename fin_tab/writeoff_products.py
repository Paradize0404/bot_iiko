"""Compute product write-off totals for bar/kitchen excluding 'Учредители'."""
from __future__ import annotations

import logging
from typing import Dict, Set

import httpx
from sqlalchemy import select

from fin_tab import iiko_auth

try:
    from db.stores_db import Store as StoreModel, async_session as stores_async_session
except Exception:  # noqa: BLE001
    StoreModel = None
    stores_async_session = None

try:
    from db.accounts_data import Account as AccountModel, async_session as accounts_async_session
except Exception:  # noqa: BLE001
    AccountModel = None
    accounts_async_session = None

logger = logging.getLogger(__name__)


async def fetch_writeoff_products_totals(date_from: str, date_to: str) -> Dict[str, float]:
    """Return sums of write-offs by segment (bar, kitchen) excluding 'Учредители'.

    Uses iiko v2 /documents/writeoff. Costs per item summed by store name containing
    "бар" → bar, "кух"/"пицц" → kitchen. Any item or document whose textual fields
    contain substring "учредител" (case-insensitive) is excluded.
    """

    token = await iiko_auth.get_auth_token()
    base_url = iiko_auth.get_base_url()

    url = f"{base_url}/resto/api/v2/documents/writeoff"
    params = {"dateFrom": date_from, "dateTo": date_to}
    headers = {"Cookie": f"key={token}"}

    try:
        async with httpx.AsyncClient(verify=False, timeout=60.0) as client:
            resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("writeoff products fetch failed: %s", exc)
        return {"bar": 0.0, "kitchen": 0.0, "total": 0.0}

    data = resp.json() or {}
    documents = data.get("response", []) or []

    # Маппинг id склада -> название (из БД), чтобы корректно разложить бар/кухню
    store_ids: Set[str] = {doc.get("storeId") for doc in documents if doc.get("storeId")}
    store_name_map: Dict[str, str] = {}
    if store_ids and stores_async_session and StoreModel:
        try:
            async with stores_async_session() as session:
                rows = await session.execute(
                    select(StoreModel.id, StoreModel.name).where(StoreModel.id.in_(store_ids))
                )
                store_name_map = {
                    store_id: (store_name or "").strip().lower()
                    for store_id, store_name in rows.all()
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("store lookup failed, fallback to names from API: %s", exc)

    # Маппинг id статьи списания -> имя статьи, чтобы исключить 'Учредители'
    account_ids: Set[str] = {doc.get("accountId") for doc in documents if doc.get("accountId")}
    account_name_map: Dict[str, str] = {}
    if account_ids and accounts_async_session and AccountModel:
        try:
            async with accounts_async_session() as session:
                rows = await session.execute(
                    select(AccountModel.id, AccountModel.name).where(AccountModel.id.in_(account_ids))
                )
                account_name_map = {
                    acc_id: (acc_name or "").strip().lower()
                    for acc_id, acc_name in rows.all()
                }
        except Exception as exc:  # noqa: BLE001
            logger.warning("account lookup failed, will rely on payload fields: %s", exc)

    def account_label(doc: dict) -> str:
        acc_id = doc.get("accountId")
        if acc_id and acc_id in account_name_map:
            return account_name_map[acc_id]
        return (doc.get("accountName") or doc.get("account") or "").strip().lower()

    def is_founders(doc: dict) -> bool:
        combined = " ".join(
            [
                account_label(doc),
                (doc.get("comment") or ""),
                (doc.get("name") or ""),
                (doc.get("type") or ""),
            ]
        ).lower()
        return "учредител" in combined

    def store_label(doc: dict) -> str:
        store_id = doc.get("storeId")
        if store_id and store_id in store_name_map:
            return store_name_map[store_id]
        store_obj = doc.get("store") or {}
        name = store_obj.get("name") or doc.get("storeName") or ""
        return name.strip().lower()

    totals = {"bar": 0.0, "kitchen": 0.0}
    skipped_founders = 0

    for doc in documents:
        if is_founders(doc):
            skipped_founders += 1
            continue

        items = doc.get("items") or []
        store = store_label(doc)
        bucket = None
        if "бар" in store:
            bucket = "bar"
        elif "кух" in store or "пицц" in store:
            bucket = "kitchen"

        if not bucket:
            continue

        doc_sum = 0.0
        for item in items:
            if isinstance(item, dict) and is_founders(item):
                skipped_founders += 1
                continue
            try:
                doc_sum += float(item.get("cost") or 0.0)
            except (TypeError, ValueError):
                continue

        totals[bucket] += doc_sum

    totals["total"] = totals["bar"] + totals["kitchen"]

    logger.info(
        "🧾 Списания продуктов: бар %.2f, кухня %.2f, всего %.2f (пропущено по 'Учредители': %d)",
        totals["bar"],
        totals["kitchen"],
        totals["total"],
        skipped_founders,
    )

    return totals
