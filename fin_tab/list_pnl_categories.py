"""Console helper: list FinTablo PnL categories."""
import asyncio
import json
import logging
from pathlib import Path

from dotenv import load_dotenv

from fin_tab.client import FinTabloClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


async def main() -> int:
    # load .env from project root if present
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    try:
        async with FinTabloClient() as cli:
            items = await cli.list_pnl_categories()
    except Exception as exc:  # pragma: no cover
        logger.error("Request failed: %s", exc)
        return 1

    print(json.dumps(items, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
