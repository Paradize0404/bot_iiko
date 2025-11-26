from __future__ import annotations
import os
import logging
from typing import List, Tuple
from sqlalchemy import String, select, Column, JSON
from sqlalchemy.orm import Mapped, mapped_column, declarative_base
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.dialects.postgresql import insert
from db.employees_db import async_session
from services.db_queries import DBQueries
from iiko.iiko_auth import get_auth_token, get_base_url
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


## ────────────── Логгер и базовые модели ──────────────
logger = logging.getLogger(__name__)
Base = declarative_base()


## ────────────── Модель шаблона приготовления ──────────────
class PreparationTemplate(Base):
    __tablename__ = "preparation_templates"
    name: Mapped[str] = mapped_column(String, primary_key=True)
    from_store_id: Mapped[str] = mapped_column(String)
    to_store_id: Mapped[str] = mapped_column(String)
    supplier_id: Mapped[str] = mapped_column(String, nullable=True)
    supplier_name: Mapped[str] = mapped_column(String, nullable=True)
    items: Mapped[dict] = mapped_column(JSON)


class WriteoffTemplate(Base):
    __tablename__ = "writeoff_templates"
    name: Mapped[str] = mapped_column(String, primary_key=True)
    store_id: Mapped[str] = mapped_column(String)
    store_name: Mapped[str] = mapped_column(String)
    account_id: Mapped[str] = mapped_column(String)
    account_name: Mapped[str] = mapped_column(String)
    reason: Mapped[str] = mapped_column(String, nullable=True)
    items: Mapped[dict] = mapped_column(JSON)


## ────────────── Модель склада ──────────────
class Store(Base):
    __tablename__ = "stores"
    id = Column(String, primary_key=True)
    name = Column(String)
    code = Column(String)
    type = Column(String)


## ────────────── Модель справочных данных ──────────────
class ReferenceData(Base):
    __tablename__ = "reference_data"
    id = Column(String, primary_key=True)
    root_type = Column(String)
    name = Column(String)
    code = Column(String)



## ────────────── Кэш складов ──────────────
STORE_CACHE: dict[str, str] = {}


## ────────────── Проверка/создание таблицы шаблонов ──────────────
async def ensure_preparation_table_exists(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        def _tables(sync_conn):
            from sqlalchemy import inspect as _inspect

            return _inspect(sync_conn).get_table_names()

        if "preparation_templates" not in await conn.run_sync(_tables):
            await conn.run_sync(Base.metadata.create_all)
            logger.info("✅ Таблица preparation_templates создана")


async def ensure_writeoff_template_table_exists(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        def _tables(sync_conn):
            from sqlalchemy import inspect as _inspect

            return _inspect(sync_conn).get_table_names()

        if "writeoff_templates" not in await conn.run_sync(_tables):
            await conn.run_sync(Base.metadata.create_all)
            logger.info("✅ Таблица writeoff_templates создана")


## ────────────── Кэширование складов ──────────────
async def preload_stores() -> None:
    global STORE_CACHE
    async with async_session() as s:
        rows = (await s.execute(select(Store.name, Store.id))).all()
        STORE_CACHE = {n.strip(): i for n, i in rows}


## ────────────── Быстрое создание клавиатуры складов ──────────────
def get_store_keyboard(variants: List[str], prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=n, callback_data=f"{prefix}:{n}")] for n in variants])


def _get_store_kb(variants: List[str], prefix: str) -> InlineKeyboardMarkup:
    return get_store_keyboard(variants, prefix)


## ────────────── Получение id склада по имени ──────────────
async def get_store_id_by_name(name: str, store_map: dict) -> str | None:
    names = store_map.get(name.strip())
    if not names:
        return None
    for n in names:
        if n.strip() in STORE_CACHE:
            return STORE_CACHE[n.strip()]
    return None


## ────────────── Поиск номенклатуры ──────────────
async def search_nomenclature(q: str, parents: list | None = None) -> list[dict]:
    return await DBQueries.search_nomenclature(q, parents=parents)


## ────────────── Поиск поставщиков ──────────────
async def search_suppliers(q: str) -> list[dict]:
    return await DBQueries.search_suppliers(q)


## ────────────── Получение имени по типу и id ──────────────
async def get_name(type_: str, id_: str) -> str:
    if not id_:
        return "—"
    async with async_session() as s:
        r = await s.execute(select(ReferenceData.name).where(ReferenceData.id == id_).where(ReferenceData.root_type == type_))
        return r.scalar_one_or_none() or "—"


## ────────────── Получение имени единицы измерения ──────────────
async def get_unit_name(unit_id: str) -> str:
    if not unit_id:
        return "шт"
    async with async_session() as s:
        r = await s.execute(select(ReferenceData.name).where(ReferenceData.id == unit_id).where(ReferenceData.root_type == "MeasureUnit"))
        return r.scalar_one_or_none() or "шт"


## ────────────── Получение списка шаблонов ──────────────
async def list_templates() -> list[str]:
    async with async_session() as s:
        r = await s.execute(select(PreparationTemplate.name))
        return r.scalars().all()


async def list_writeoff_templates() -> list[str]:
    async with async_session() as s:
        r = await s.execute(select(WriteoffTemplate.name))
        return r.scalars().all()


async def get_writeoff_template(name: str):
    async with async_session() as s:
        r = await s.execute(select(WriteoffTemplate).where(WriteoffTemplate.name == name))
        return r.scalar_one_or_none()


## ────────────── Получение шаблона по имени ──────────────
async def get_template(name: str):
    async with async_session() as s:
        r = await s.execute(select(PreparationTemplate).where(PreparationTemplate.name == name))
        return r.scalar_one_or_none()


## ────────────── Формирование XML для расходной накладной ──────────────
def build_invoice_xml(template: dict) -> str:
    from datetime import datetime
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    num = "tg-inv-" + datetime.now().strftime("%Y%m%d%H%M%S")
    items = "".join([f"\n        <item>\n            <productId>{it['id']}</productId>\n            <storeId>{template['to_store_id']}</storeId>\n            <price>{float(it['price']):.2f}</price>\n            <amount>{float(it['quantity']):.2f}</amount>\n            <sum>{round(float(it['price'])*float(it['quantity']),2):.2f}</sum>\n        </item>" for it in template['items']])
    return f"""<?xml version='1.0' encoding='UTF-8'?>\n<document>\n    <documentNumber>{num}</documentNumber>\n    <dateIncoming>{now}</dateIncoming>\n    <status>PROCESSED</status>\n    <useDefaultDocumentTime>true</useDefaultDocumentTime>\n    <revenueAccountCode>4.08</revenueAccountCode>\n    <counteragentId>{template['supplier_id']}</counteragentId>\n    <defaultStoreId>{template['to_store_id']}</defaultStoreId>\n    <conceptionId>{os.getenv('PIZZAYOLO_CONCEPTION_ID','cd6b8810-0f57-4e1e-82a4-3f60fb2ded7a')}</conceptionId>\n    <comment>Создано автоматически через Telegram-бота</comment>\n    <items>{items}\n    </items>\n</document>"""


## ────────────── Отправка XML в iiko ──────────────
async def post_xml(path: str, xml: str) -> Tuple[bool, str]:
    DRY_RUN = os.getenv("DRY_RUN", "false").lower() in ("1", "true", "yes")
    if DRY_RUN:
        logger.info("DRY_RUN=true — симулируем отправку в iiko: %s", path)
        return True, "DRY_RUN — симуляция"
    try:
        token = await get_auth_token()
    except Exception as e:
        return False, f"Авторизация не удалась: {e}"
    url = f"{get_base_url()}{path}"
    headers = {"Content-Type": "application/xml"}
    params = {"key": token}
    import httpx
    async with httpx.AsyncClient(verify=False) as cli:
        try:
            r = await cli.post(url, params=params, content=xml, headers=headers)
            r.raise_for_status()
            return True, r.text
        except Exception as e:
            txt = getattr(e, 'response', None)
            if txt is not None and hasattr(txt, 'text'):
                return False, txt.text
            return False, str(e)

# Compatibility wrappers for existing handlers
from config import STORE_NAME_MAP

## ────────────── Быстрое создание клавиатуры (совместимость) ──────────────
def _kbd(variants: List[str], prefix: str) -> InlineKeyboardMarkup:
    return get_store_keyboard(variants, prefix)

## ────────────── Получение id склада (совместимость) ──────────────
async def _get_store_id(name: str) -> str | None:
    return await get_store_id_by_name(name, STORE_NAME_MAP)

# Alias for existing handlers
## ────────────── Получение имени единицы измерения (совместимость) ──────────────
async def get_unit_name_by_id(unit_id: str) -> str:
    return await get_unit_name(unit_id)

## ────────────── Клавиатура выбора шаблона ──────────────
def get_template_keyboard(templates: List[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=f"use_template:{t}")] for t in templates])
