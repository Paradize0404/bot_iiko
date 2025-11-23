
## ────────────── Сервис централизованных запросов к базе данных ──────────────

from sqlalchemy import select
from db.employees_db import async_session, Employee
from db.nomenclature_db import Nomenclature
from db.sprav_db import ReferenceData as Accounts
from config import PARENT_FILTERS


## ────────────── Класс сервисных запросов к базе данных ──────────────
class DBQueries:
    """Database query service for common operations."""

    ## ────────────── Получение сотрудника по Telegram ID ──────────────
    @staticmethod
    async def get_employee_by_telegram(tg_id: str) -> Employee | None:
        """Get employee by Telegram ID."""
        async with async_session() as session:
            result = await session.execute(
                select(Employee).where(Employee.telegram_id == str(tg_id))
            )
            return result.scalar_one_or_none()

    ## ────────────── Получение счетов по именам ──────────────
    @staticmethod
    async def get_accounts_by_names(names: list[str]) -> list[Accounts]:
        """Get accounts (payment types) by names."""
        async with async_session() as session:
            result = await session.execute(
                select(Accounts).where(Accounts.name.in_(names))
            )
            return result.scalars().all()

    ## ────────────── Поиск номенклатуры ──────────────
    @staticmethod
    async def search_nomenclature(
        partial_name: str,
        types: list[str] | None = None,
        parents: list[str] | None = None,
        limit: int = 50
    ) -> list[dict]:
        """
        Universal nomenclature search with optional filters.
        
        Args:
            partial_name: Search term(s) - words separated by spaces
            types: Filter by nomenclature types (e.g., ["GOODS", "PREPARED"])
            parents: Filter by parent IDs (defaults to PARENT_FILTERS if None)
            limit: Max results
        
        Returns:
            List of dicts with id, name, mainunit, type
        """
        async with async_session() as session:
            terms = [t.strip() for t in partial_name.lower().split() if t.strip()]
            if not terms:
                return []

            query = select(
                Nomenclature.id,
                Nomenclature.name,
                Nomenclature.mainunit,
                Nomenclature.type
            ).limit(limit)

            # Apply type filter if specified
            if types:
                query = query.where(Nomenclature.type.in_(types))

            # Apply parent filter (defaults to PARENT_FILTERS)
            if parents is not None:
                query = query.where(Nomenclature.parent.in_(parents))
            else:
                query = query.where(Nomenclature.parent.in_(PARENT_FILTERS))

            # Apply text search for all terms
            for term in terms:
                query = query.where(Nomenclature.name.ilike(f"%{term}%"))

            result = await session.execute(query)
            rows = result.all()

            return [
                {
                    "id": r.id,
                    "name": r.name,
                    "mainunit": r.mainunit,
                    "type": r.type
                }
                for r in rows
            ]

    ## ────────────── Поиск поставщиков ──────────────
    @staticmethod
    async def search_suppliers(partial_name: str, limit: int = 50) -> list[dict]:
        """Search suppliers by name."""
        from db.supplier_db import Supplier
        
        async with async_session() as session:
            terms = [t.strip().lower() for t in partial_name.split() if t.strip()]
            if not terms:
                return []

            query = select(Supplier.id, Supplier.name).limit(limit)
            for term in terms:
                query = query.where(Supplier.name.ilike(f"%{term}%"))

            result = await session.execute(query)
            rows = result.all()
            return [{"id": r.id, "name": r.name} for r in rows]
