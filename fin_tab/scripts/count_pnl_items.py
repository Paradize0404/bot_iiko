import asyncio
from pathlib import Path

from dotenv import load_dotenv

import sys
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fin_tab.client import FinTabloClient

async def main(date_str: str) -> None:
    load_dotenv(ROOT / ".env")
    async with FinTabloClient() as cli:
        items = await cli.list_pnl_items(date=date_str)
    print(f"date={date_str} count={len(items)}")

if __name__ == "__main__":
    date_arg = sys.argv[1] if len(sys.argv) > 1 else "01.2026"
    asyncio.run(main(date_arg))
