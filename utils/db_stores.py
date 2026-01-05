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
            # Фоллбек на стандартные PG_* переменные
            pg_user = os.getenv("PGUSER") or os.getenv("POSTGRES_USER")
            pg_password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD")
            pg_host = os.getenv("PGHOST") or os.getenv("POSTGRES_HOST") or "localhost"
            pg_port = os.getenv("PGPORT") or os.getenv("POSTGRES_PORT") or "5432"
            pg_db = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB")
            if pg_user and pg_password and pg_db:
                dsn = f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_db}"

        if not dsn:
            raise RuntimeError("DATABASE_URL не указан и не удалось собрать DSN из PG* переменных")

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
