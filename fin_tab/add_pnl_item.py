"""Console helper: add a PnL item for a given month."""
import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv

from fin_tab.client import FinTabloClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add PnL item to FinTablo")
    parser.add_argument("category_id", type=int, help="ID статьи ПиУ")
    parser.add_argument("value", type=float, help="Сумма без НДС")
    parser.add_argument("date", type=str, help="Месяц в формате MM.YYYY")
    parser.add_argument("--nds", type=float, default=None, help="НДС (опционально)")
    parser.add_argument("--direction-id", type=int, default=None, help="ID направления (опционально)")
    parser.add_argument("--comment", type=str, default=None, help="Комментарий")
    parser.add_argument("--url", type=str, default=None, help="Ссылка на документ")
    return parser.parse_args()


def build_payload(ns: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "categoryId": ns.category_id,
        "value": ns.value,
        "date": ns.date,
    }
    if ns.nds is not None:
        payload["nds"] = ns.nds
    if ns.direction_id is not None:
        payload["directionId"] = ns.direction_id
    if ns.comment:
        payload["comment"] = ns.comment
    if ns.url:
        payload["url"] = ns.url
    return payload


async def main() -> int:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    ns = parse_args()
    payload = build_payload(ns)
    try:
        async with FinTabloClient() as cli:
            item = await cli.create_pnl_item(payload)
    except Exception as exc:  # pragma: no cover
        logger.error("Request failed: %s", exc)
        return 1

    print(json.dumps(item, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
