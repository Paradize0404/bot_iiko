## ────────────── Утилита работы с пулом соединений БД ──────────────
import os, asyncpg
from typing import Iterable, Sequence
from dotenv import load_dotenv

load_dotenv()
_POOL: asyncpg.Pool | None = None


## ────────────── Инициализация пула соединений ──────────────
async def init_pool() -> None:
    """Создаём пул при старте бота (один раз)."""
    global _POOL
    
    if _POOL is None:
        dsn = os.getenv("DATABASE_URL")
        if dsn and "+asyncpg" in dsn:
            dsn = dsn.replace("+asyncpg", "")

        if not dsn:
            raise RuntimeError("DATABASE_URL не указан")

        _POOL = await asyncpg.create_pool(dsn, min_size=1, max_size=10)


def get_pool() -> asyncpg.Pool:
    """Получить пул соединений"""
    if _POOL is None:
        raise RuntimeError("Пул соединений не инициализирован. Вызовите init_pool() сначала.")
    return _POOL


## ────────────── Функции выборки складов ──────────────

async def fetch_by_names(names: Iterable[str]) -> list[tuple[str, str]]:
    """
    Вернуть [(id, name), …] по списку читабельных названий («Кухня», …).
    """
    rows = await _POOL.fetch(
        "SELECT id, name FROM stores WHERE name = ANY($1::text[]) ORDER BY name;",
        list(names),
    )
    return [(r["id"], r["name"]) for r in rows]


async def fetch_by_ids(ids: Sequence[str]) -> list[tuple[str, str]]:
    """
    То же, но по UUID; пригодится, если захотите хранить связи в таблице.
    """
    rows = await _POOL.fetch(
        "SELECT id, name FROM stores WHERE id = ANY($1::uuid[]) ORDER BY name;",
        list(ids),
    )
    return [(r["id"], r["name"]) for r in rows]
