from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterable, Mapping, Sequence

import httpx

from iiko.iiko_auth import get_auth_token, get_base_url

logger = logging.getLogger(__name__)


class TransferValidationError(ValueError):
    """Raised when transfer payload is missing required fields."""


RequiredItemFields = ("productId", "amount", "measureUnitId")


def _normalize_item(item: Mapping[str, object]) -> dict[str, object]:
    product_id = item.get("productId") or item.get("id")
    measure_unit_id = item.get("measureUnitId") or item.get("mainunit")
    amount = item.get("amount") or item.get("quantity")
    if not product_id or not measure_unit_id or amount is None:
        raise TransferValidationError(f"Некорректный товар для перемещения: {item}")
    amount_value = float(amount)
    if amount_value <= 0:
        raise TransferValidationError(f"Количество должно быть > 0 для {item}")
    return {
        "productId": product_id,
        "amount": amount_value,
        "measureUnitId": measure_unit_id,
    }


def build_transfer_document(
    *,
    store_from_id: str,
    store_to_id: str,
    items: Sequence[Mapping[str, object]] | Iterable[Mapping[str, object]],
    comment: str = "",
    status: str = "PROCESSED",
    date_incoming: datetime | None = None,
) -> dict[str, object]:
    if not store_from_id or not store_to_id:
        raise TransferValidationError("store_from_id и store_to_id обязательны")
    normalized_items = [_normalize_item(item) for item in items]
    if not normalized_items:
        raise TransferValidationError("Нужно добавить хотя бы одну позицию")
    date_value = (date_incoming or datetime.now()).strftime("%Y-%m-%dT%H:%M:%S")
    return {
        "dateIncoming": date_value,
        "status": status,
        "comment": comment or "",
        "storeFromId": store_from_id,
        "storeToId": store_to_id,
        "items": normalized_items,
    }


async def send_internal_transfer(
    *,
    store_from_id: str,
    store_to_id: str,
    items: Sequence[Mapping[str, object]] | Iterable[Mapping[str, object]],
    comment: str = "",
    status: str = "PROCESSED",
    date_incoming: datetime | None = None,
    timeout: float = 30.0,
) -> str:
    document = build_transfer_document(
        store_from_id=store_from_id,
        store_to_id=store_to_id,
        items=items,
        comment=comment,
        status=status,
        date_incoming=date_incoming,
    )
    token = await get_auth_token()
    base_url = get_base_url()
    url = f"{base_url}/resto/api/v2/documents/internalTransfer"
    params = {"key": token}
    async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
        response = await client.post(url, params=params, json=document)
        response.raise_for_status()
        logger.info(
            "📤 Отправлено перемещение %s → %s (%d позиций)",
            store_from_id,
            store_to_id,
            len(document["items"]),
        )
        return response.text or ""
