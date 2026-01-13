import asyncio
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

# project root
ROOT = Path(__file__).resolve().parents[2]
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fin_tab.client import FinTabloClient

DATE = "01.2026"


def fmt(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ")


def enrich_items(items: list[dict]) -> dict:
    by_dir: dict[int | None, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for it in items:
        dir_id = it.get("directionId")
        pnl_type = it.get("pnlType") or it.get("type") or "unknown"
        val = float(it.get("value") or 0)
        by_dir[dir_id][pnl_type] += val
    return by_dir


async def main() -> None:
    load_dotenv(ROOT / ".env")
    async with FinTabloClient() as cli:
        items = await cli.list_pnl_items(date=DATE)
    by_dir = enrich_items(items)
    totals = sorted(
        ((abs(sum(mp.values())), d) for d, mp in by_dir.items()),
        reverse=True,
    )
    for _, d in totals[:15]:
        mp = by_dir[d]
        total = sum(mp.values())
        print(f"directionId={d} total={fmt(total)}")
        for t, v in sorted(mp.items()):
            print(f"  {t}: {fmt(v)}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
