"""Simple FinTablo API client for FinTablo endpoints."""
import os
from typing import Any, Dict, List, Optional

import httpx

DEFAULT_BASE_URL = "https://api.fintablo.ru"


class FinTabloClient:
    def __init__(self, token: Optional[str] = None, base_url: Optional[str] = None) -> None:
        self.token = token or os.getenv("FIN_TABLO_TOKEN")
        if not self.token:
            raise RuntimeError("FIN_TABLO_TOKEN is required for FinTablo API calls")
        self.base_url = (base_url or os.getenv("FIN_TABLO_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "FinTabloClient":
        self._client = httpx.AsyncClient(base_url=self.base_url, headers=self._headers(), timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client:
            await self._client.aclose()

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def list_employees(self, **query: Any) -> List[Dict[str, Any]]:
        """GET /v1/employees"""
        if not self._client:
            raise RuntimeError("Client not initialized; use 'async with FinTabloClient()' or call connect().")
        resp = await self._client.get("/v1/employees", params=query)
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])

    async def list_pnl_categories(self, **query: Any) -> List[Dict[str, Any]]:
        """GET /v1/pnl-category"""
        if not self._client:
            raise RuntimeError("Client not initialized; use 'async with FinTabloClient()' or call connect().")
        resp = await self._client.get("/v1/pnl-category", params=query)
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])

    async def list_pnl_items(self, **query: Any) -> List[Dict[str, Any]]:
        """GET /v1/pnl-item"""
        if not self._client:
            raise RuntimeError("Client not initialized; use 'async with FinTabloClient()'.")
        resp = await self._client.get("/v1/pnl-item", params=query)
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])

    async def list_directions(self, **query: Any) -> List[Dict[str, Any]]:
        """GET /v1/direction"""
        if not self._client:
            raise RuntimeError("Client not initialized; use 'async with FinTabloClient()'.")
        resp = await self._client.get("/v1/direction", params=query)
        resp.raise_for_status()
        data = resp.json()
        return data.get("items", [])

    async def create_pnl_item(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST /v1/pnl-item"""
        if not self._client:
            raise RuntimeError("Client not initialized; use 'async with FinTabloClient()'.")
        resp = await self._client.post("/v1/pnl-item", json=payload)
        if resp.status_code >= 400:
            try:
                detail = resp.text
            except Exception:  # pragma: no cover
                detail = ""
            raise httpx.HTTPStatusError(
                f"FinTablo error {resp.status_code}: {detail}", request=resp.request, response=resp
            )
        data = resp.json()
        items = data.get("items", [])
        return items[0] if items else {}

    async def delete_pnl_item(self, item_id: int) -> None:
        """DELETE /v1/pnl-item/{id}"""
        if not self._client:
            raise RuntimeError("Client not initialized; use 'async with FinTabloClient()'.")
        resp = await self._client.delete(f"/v1/pnl-item/{item_id}")
        if resp.status_code >= 400:
            try:
                detail = resp.text
            except Exception:  # pragma: no cover
                detail = ""
            raise httpx.HTTPStatusError(
                f"FinTablo error {resp.status_code}: {detail}", request=resp.request, response=resp
            )
